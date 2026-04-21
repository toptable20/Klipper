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
        x_p = 0.15
        y_p = 0.15
        w_p = 0.45
        h_p = 0.7

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

        # for moving average
        self.moving_avg_center = None
        self.alpha = 0.2

        # blur filter size
        self.k_size = 5
        self.sig_x = 0

        self.input_height = 0
        self.input_radius = 0
        self.available_cameras = []

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
        
        # print(f"Dist calculation: Center({cx_px}, {cy_px}) -> Target({tx_px}, {ty_px})")
        
        return int(round(pixel_radius))
    
    def calc_calib_coord(self, camshow = False):
        logging.info("Starting bed center calibration...")
        try:
            cap = cv2.VideoCapture(self.get_available_cameras()[0])
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.get_camera_size()[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.get_camera_size()[1])
            logging.info(f"cam size: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)    # 자동 노출 비활성화

            # OV9732
            cap.set(cv2.CAP_PROP_EXPOSURE, 1500)        # 예시 값

        except Exception as e:
            logging.error(f"Failed to open camera: {e}")
            return "Failed to open camera"
        
        self.calib_coord = None

        self.camera_matrix, self.dist_coeffs, self.h_matrix, self.delta_h = self.load_calibration_data(CALIB_FILE_PATH)

        self.h_matrix_new = self.h_matrix + (self.input_height * self.delta_h)
        if self.h_matrix_new[2, 2] != 0:
            self.h_matrix_new = self.h_matrix_new / self.h_matrix_new[2, 2]

        logging.info("Start capturing images")
        fail_count = 0
        success_count = 0
        self.moving_avg_center = None
        while fail_count < 10 and cap.isOpened():
            ret, frame = cap.read()
            logging.info("Captured image")
            if not ret:
                logging.warning("Failed to capture image from camera.")
                fail_count += 1
                continue

            img_undistorted = cv2.undistort(frame, self.camera_matrix, self.dist_coeffs, None, self.camera_matrix)

            printbed_roi = self.get_camera_roi()
            img_roi = img_undistorted[printbed_roi[1]:printbed_roi[1]+printbed_roi[3], printbed_roi[0]:printbed_roi[0]+printbed_roi[2]]

            self.min_radius = np.clip(self.mm_to_pixel_radius(self.target_r_mm - self.margin, self.h_matrix_new), 0, 100)
            self.max_radius = np.clip(self.mm_to_pixel_radius(self.target_r_mm + self.margin, self.h_matrix_new), 0, 100)

            logging.info("Copying frame")
            frame2 = img_undistorted.copy()
            img_roi = cv2.GaussianBlur(img_roi, (self.k_size, self.k_size), self.sig_x)
            gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
            circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, 10000, param1 = self.param1, param2 = self.param2, minRadius = self.min_radius, maxRadius = self.max_radius)
            cv2.rectangle(frame2, (printbed_roi[0], printbed_roi[1]), (printbed_roi[0]+printbed_roi[2], printbed_roi[1]+printbed_roi[3]), 255, 1)

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
                cx = i[0] + printbed_roi[0]   # ROI의 x offset 추가
                cy = i[1] + printbed_roi[1]   # ROI의 y offset 추가
                cv2.circle(frame2, (int(cx), int(cy)), int(i[2]), (0,0,255), 2)  #원 그리기
                cv2.circle(frame2, (int(cx), int(cy)), 2, (0,0,255), 3)  #원 그리기

            logging.info(f"Circle center: ({cx}, {cy})")

            img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_success_{success_count}.png"
            cv2.imwrite(img_filename, frame2)
            logging.info(f"Saved image: {img_filename}")
            success_count += 1

            pixel_coord = np.array([[cx, cy]], dtype=np.float32)
            self.calib_coord = cv2.perspectiveTransform(np.array([pixel_coord]), self.h_matrix_new)

            self.moving_avg_center = (self.alpha * self.calib_coord[0][0]) + (1 - self.alpha) * (self.moving_avg_center if self.moving_avg_center is not None else self.calib_coord[0][0])

            logging.info(f"calculated pos: {self.moving_avg_center}")
            # if camshow:
            #     s_width = int(frame2.shape[1] * self.resize_percent / 100)
            #     s_height = int(frame2.shape[0] * self.resize_percent / 100)
            #     dim = (s_width, s_height)
            #     frame2 = cv2.resize(frame2, dim, interpolation = cv2.INTER_AREA)
            #     cv2.imshow('frame', frame2)

            if success_count >= 5:
                break          
        
        cap.release()

        if fail_count >= 10:
            logging.error("Bed center calibration failed after multiple attempts.")
            return "Failed to detect circle"

        return self.moving_avg_center
    
def load_config(config):
    return BedCenterCalibration(config)