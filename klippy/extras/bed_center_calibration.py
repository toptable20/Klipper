import os
import cv2
import numpy as np
import json

import logging

CALIB_FILE_PATH = "/home/mks/printer_data/config/calib_config.json"

class BedCenterCalibration:
    def __init__(self, config):
        
        # 720p
        self.camera_width = 1280
        self.camera_height = 720

        # roi ratios
        x_p = 0.30
        y_p = 0.22
        w_p = 0.28
        h_p = 0.49

        self.camera_roi = (int(self.camera_width*x_p), int(self.camera_height*y_p), int(self.camera_width*w_p), int(self.camera_height*h_p))  # x, y, w, h

        available_cameras = []
        max_devices = 5
        self.resize_percent = 40  # for camera display window

        # OV9732
        # target: 18cm circle
        self.param1 = 30
        self.param2 = 10
        self.min_radius = 100
        self.max_radius = 200
        self.target_r_mm = 50
        self.margin = 10

        self.moving_avg_center = [None] * 20
        self.alpha = 0.2

        # blur filter size
        self.k_size = 5
        self.sig_x = 0

        self.input_height = 0
        self.input_radius = 0
        self.available_cameras = []

        self.target_number = 0
        self.print_sequence = 0
        self.detect_type = 0  # 'circle' or 'unstructured'

        for i in range(max_devices):
            device_path = f"/dev/video{i}"
            if os.path.exists(device_path):
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    available_cameras.append(i)
                    cap.release()
        
        self.available_cameras = available_cameras
        logging.info(f"Available cameras for bed center calibration: {self.available_cameras}")
        logging.info(f"cam size: {self.camera_width}x{self.camera_height}")

    def get_is_available_camera(self):
        if self.available_cameras:
            return True
        else:
            return False

    def load_calibration_data(self, file_path):
        with open(file_path, 'r') as f:
            data = json.load(f)

        # 리스트를 다시 numpy array로 변환 (float32 권장)
        mtx = np.array(data['mtx'], dtype=np.float32)
        dist = np.array(data['dist'], dtype=np.float32)
        h_matrix = np.array(data['h0'], dtype=np.float32)
        href = np.array(data['href'], dtype=np.float32)
        ref_h = float(data['ref_h'])

        if ref_h <= 0:
            raise ValueError("Reference height(ref_h) must be greater than 0.")
        
        delta_h = (href-h_matrix) / ref_h

        return mtx, dist, h_matrix, delta_h

    def get_camera_roi(self):
        return self.camera_roi

    def get_camera_size(self):
        return self.camera_width, self.camera_height

    def get_available_cameras(self):
        return self.available_cameras
    
    def get_h_matrix(self):
        return self.h_matrix
    
    def get_h_matrix_new(self):
        return self.h_matrix_new
    
    def set_height(self, height):
        self.input_height = height
    
    def set_radius(self, radius):
        if radius - self.margin < 0:
            radius = self.margin
        self.target_r_mm = radius

    def set_target_number(self, number):
        self.target_number = number

    def set_print_sequence(self, sequence):
        self.print_sequence = sequence

    def set_detect_type(self, detect_type):
        self.detect_type = detect_type

    def mm_to_pixel_radius(self, radius_mm, h_matrix_new):
        h_pixel_to_world = h_matrix_new
        h_world_to_pixel = np.linalg.inv(h_matrix_new)
        
        cx_px = self.camera_width / 2
        cy_px = self.camera_height / 2
        
        center_px = np.array([[[cx_px, cy_px]]], dtype=np.float32)
        center_world = cv2.perspectiveTransform(center_px, h_pixel_to_world)
        wx, wy = center_world[0][0]
        
        target_world = np.array([[[wx + float(radius_mm), wy]]], dtype=np.float32)
        
        target_px = cv2.perspectiveTransform(target_world, h_world_to_pixel)
        tx_px, ty_px = target_px[0][0]
        
        pixel_radius = np.sqrt((tx_px - cx_px)**2 + (ty_px - cy_px)**2)
        
        return int(round(pixel_radius))

    def _detect_circle(self, cap):
        printbed_roi = self.get_camera_roi()
        fail_count = 0
        success_count = 0
        moving_avg = None

        while fail_count < 10 and cap.isOpened():
            ret, frame = cap.read()
            logging.info("Captured image")
            if not ret:
                logging.warning("Failed to capture image from camera.")
                fail_count += 1
                continue

            img_undistorted = cv2.undistort(frame, self.camera_matrix, self.dist_coeffs, None, self.camera_matrix)
            img_roi = img_undistorted[printbed_roi[1]:printbed_roi[1]+printbed_roi[3], printbed_roi[0]:printbed_roi[0]+printbed_roi[2]]

            min_r = np.clip(self.mm_to_pixel_radius(self.target_r_mm - self.margin, self.h_matrix_new), 0, 100)
            max_r = np.clip(self.mm_to_pixel_radius(self.target_r_mm + self.margin, self.h_matrix_new), 0, 100)

            img_roi = cv2.GaussianBlur(img_roi, (self.k_size, self.k_size), self.sig_x)
            gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
            circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, 10000,
                                       param1=self.param1, param2=self.param2,
                                       minRadius=min_r, maxRadius=max_r)

            frame2 = img_undistorted.copy()
            cv2.rectangle(frame2, (printbed_roi[0], printbed_roi[1]),
                          (printbed_roi[0]+printbed_roi[2], printbed_roi[1]+printbed_roi[3]), 255, 1)

            if circles is None or circles.shape[-1] != 3:
                logging.warning("No circles detected, retrying...")
                img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_fail_{fail_count}.png"
                cv2.imwrite(img_filename, frame2)
                logging.info(f"Saved image: {img_filename}")
                fail_count += 1
                continue

            logging.info(f"Circles detected {circles}")
            cx = cy = 0
            for i in circles[0]:
                cx = i[0] + printbed_roi[0]
                cy = i[1] + printbed_roi[1]
                cv2.circle(frame2, (int(cx), int(cy)), int(i[2]), (0, 0, 255), 2)
                cv2.circle(frame2, (int(cx), int(cy)), 2, (0, 0, 255), 3)

            logging.info(f"Circle center: ({cx}, {cy})")
            img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_success_{success_count}.png"
            cv2.imwrite(img_filename, frame2)
            logging.info(f"Saved image: {img_filename}")
            success_count += 1

            pixel_coord = np.array([[cx, cy]], dtype=np.float32)
            calib_coord = cv2.perspectiveTransform(np.array([pixel_coord]), self.h_matrix_new)

            moving_avg = (self.alpha * calib_coord[0][0]) + \
                         (1 - self.alpha) * (moving_avg if moving_avg is not None else calib_coord[0][0])
            logging.info(f"calculated pos: {moving_avg}")

            if success_count >= 5:
                break

        if fail_count >= 10:
            logging.error("Circle detection failed after multiple attempts.")
            return "Failed to detect circle"

        if moving_avg is not None:
            return [moving_avg.tolist()]  # 다중 좌표 포맷과 통일: [[x, y]]
        return "Failed to detect circle"

    def _detect_contour(self, cap):
        printbed_roi = self.get_camera_roi()
        self.moving_avg_center = [None] * 20

        fail_count = 0
        success_count = 0
        detected_groups = []
        try_count = 0

        while fail_count < 10 and cap.isOpened():
            try_count += 1
            ret, frame = cap.read()
            logging.info("Captured image")
            if not ret:
                logging.warning("Failed to capture image from camera.")
                fail_count += 1
                continue

            img_undistorted = cv2.undistort(frame, self.camera_matrix, self.dist_coeffs, None, self.camera_matrix)
            img_roi = img_undistorted[printbed_roi[1]:printbed_roi[1]+printbed_roi[3], printbed_roi[0]:printbed_roi[0]+printbed_roi[2]]

            gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            edges = cv2.Canny(blurred, 50, 95)

            kernel = np.ones((3, 3), np.uint8)
            edges = cv2.dilate(edges, kernel, iterations=5)
            edges = cv2.erode(edges, kernel, iterations=5)

            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            target_centers = []
            contours = sorted(contours, key=lambda c: cv2.boundingRect(c)[0])
            cv2.rectangle(img_undistorted, (printbed_roi[0], printbed_roi[1]),
                          (printbed_roi[0]+printbed_roi[2], printbed_roi[1]+printbed_roi[3]), (255, 0, 0), 1)

            for cnt in contours:
                area = cv2.contourArea(cnt)
                logging.info(f"Contour area: {area}")
                if area < 4500 or area > 50000:
                    continue

                M = cv2.moments(cnt)
                if M['m00'] == 0:
                    continue

                mc_x = M["m10"] / M["m00"]
                mc_y = M["m01"] / M["m00"]

                epsilon = 0.005 * cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, epsilon, True)
                bound_rect = cv2.boundingRect(approx)

                if bound_rect[2] < 5 or bound_rect[3] < 50:
                    continue

                (x, y, w, h) = bound_rect

                cv2.rectangle(img_undistorted,
                              (printbed_roi[0] + x, printbed_roi[1] + y),
                              (printbed_roi[0] + x + w, printbed_roi[1] + y + h), (0, 0, 255), 2)

                color = (0, 0, 255)
                offset = 5

                abs_x = mc_x + (printbed_roi[0] if printbed_roi else 0)
                abs_y = mc_y + (printbed_roi[1] if printbed_roi else 0)

                cv2.drawContours(img_undistorted,
                                 [approx + np.array([[printbed_roi[0], printbed_roi[1]]])], 0, (0, 255, 0), 2)
                cv2.circle(img_undistorted, (int(abs_x), int(abs_y)), 3, color, -1, 8, 0)

                print("center: ", abs_x, abs_y)
                cv2.rectangle(
                    img_undistorted,
                    (int(x + (printbed_roi[0] if printbed_roi else 0) - offset),
                     int(y + (printbed_roi[1] if printbed_roi else 0) - offset)),
                    (int(x + w + (printbed_roi[0] if printbed_roi else 0) + offset),
                     int(y + h + (printbed_roi[1] if printbed_roi else 0) + offset)),
                    color, 1
                )

                target_centers.append((abs_x, abs_y))

            if target_centers:
                threshold = 50
                for pt in target_centers:
                    matched = False
                    for group in detected_groups:
                        avg_x = sum(p[0] for p in group) / len(group)
                        avg_y = sum(p[1] for p in group) / len(group)
                        dist = ((pt[0] - avg_x)**2 + (pt[1] - avg_y)**2)**0.5
                        if dist < threshold:
                            group.append(pt)
                            matched = True
                            break
                    if not matched:
                        detected_groups.append([pt])
                logging.info(f"Frame {success_count} processed. Groups: {len(detected_groups)}")
            else:
                logging.warning("No targets detected in this frame.")
                fail_count += 1

            final_calib_coords = []
            for group in detected_groups:
                if len(group) >= 3:
                    avg_px = sum(p[0] for p in group) / len(group)
                    avg_py = sum(p[1] for p in group) / len(group)
                    final_calib_coords.append((avg_px, avg_py))

            if self.target_number > 0:
                scored = sorted(
                    [(g, sum(p[0] for p in g)/len(g), sum(p[1] for p in g)/len(g))
                    for g in detected_groups if len(g) >= 3],
                    key=lambda x: len(x[0]), reverse=True
                )[:self.target_number]
                final_calib_coords = [(s[1], s[2]) for s in scored]

            logging.info(f"Detected target centers: {final_calib_coords}")

            if try_count >= 3:
                if len(final_calib_coords) == 0:
                    logging.warning("No contours detected, retrying...")
                    img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_fail_{fail_count}.png"
                    cv2.imwrite(img_filename, img_undistorted)
                    logging.info(f"Saved image: {img_filename}")
                    fail_count += 1
                    continue

                img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_success_{success_count}.png"
                cv2.imwrite(img_filename, img_undistorted)
                logging.info(f"Saved image: {img_filename}")
                success_count += 1

                target_idx = 0
                for center in final_calib_coords:
                    cx, cy = center
                    if self.moving_avg_center[target_idx] is None:
                        new_avg = np.array([cx, cy], dtype=np.float64)
                    else:
                        new_avg = (self.alpha * np.array([cx, cy], dtype=np.float64)
                                   + (1 - self.alpha) * self.moving_avg_center[target_idx])
                    self.moving_avg_center[target_idx] = new_avg
                    logging.info(f"pixel EMA[{target_idx}]: {self.moving_avg_center[target_idx]}")
                    target_idx += 1

            if success_count >= 5:
                break

        if fail_count >= 10:
            logging.error("Contour detection failed after multiple attempts.")
            return "Failed to detect targets"

        world_list = []
        for item in self.moving_avg_center:
            if item is not None:
                px_avg = np.array([[[float(item[0]), float(item[1])]]], dtype=np.float32)
                world_pt = cv2.perspectiveTransform(px_avg, self.get_h_matrix_new())[0][0]
                world_list.append(world_pt.tolist())

        world_list.sort(key=lambda p: p[0])
        logging.info(f"Final Clean Coords: {world_list}")
        return world_list

    def calc_calib_coord(self):
        logging.info("Starting bed center calibration...")
        try:
            cap = cv2.VideoCapture(self.get_available_cameras()[0])
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.get_camera_size()[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.get_camera_size()[1])
            logging.info(f"cam size: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)    # 자동 노출 비활성화
            cap.set(cv2.CAP_PROP_EXPOSURE, 1500)
        except Exception as e:
            logging.error(f"Failed to open camera: {e}")
            return "Failed to open camera"

        self.camera_matrix, self.dist_coeffs, self.h_matrix, self.delta_h = self.load_calibration_data(CALIB_FILE_PATH)
        self.h_matrix_new = self.h_matrix + (self.input_height * self.delta_h)
        if self.h_matrix_new[2, 2] != 0:
            self.h_matrix_new = self.h_matrix_new / self.h_matrix_new[2, 2]

        logging.info(f"Start capturing images (detect_type={self.detect_type})")

        if self.detect_type == 0:  # circle mode
            result = self._detect_circle(cap)
        else:
            result = self._detect_contour(cap)

        cap.release()
        return result

def load_config(config):
    return BedCenterCalibration(config)
