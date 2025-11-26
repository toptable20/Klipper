import os
import cv2
import numpy as np

import logging

# Known calibration points for bed center calibration
# 1.0
# known_printer_points_mm = [
#     (130, 140), (95, 140), (130.50, 177),
#     (112, 160), (149, 160), (167, 141),
#     (151, 125), (130.5, 105), (115, 122)
# ]

# known_camera_points_px = [
#     (323.5, 180.5), (228.5, 183.5), (322.5, 80.5),
#     (273.5, 129.5), (373.5, 125.5), (422.5, 175.5),
#     (382.5, 223.5), (330.5, 279.5), (287.5, 233.5)
# ]

# 3.0 25.11.18 heyan
# known_printer_points_mm = [
#     (175, 20), (100, 20), (30, 20),
#     (175, 100), (100, 100), (30, 100),
#     (175, 175), (100, 175), (30, 175)
# ]

# known_camera_points_px = [
#     (640.5, 257.5), (865.5, 262.5), (1080.5, 262.5),
#     (637.5, 505.5), (864.5, 510.5), (1080.5, 512.5),
#     (637.5, 732.5), (864.5, 737.5), (1077.5, 743.5)
# ]

# 3.0 new cam
known_printer_points_mm = [
    (175, 20), (100, 20), (30, 20),
    (175, 100), (100, 100), (30, 100),
    (175, 175), (100, 175), (30, 175)
]

known_camera_points_px = [
    (472.5, 268.5), (725.5, 265.5), (966.5, 262.5),
    (476.5, 544.5), (731.5, 540.5), (969.5, 538.5),
    (479.5, 798.5), (732.5, 800.5), (970.5, 796.5)
]

class BedCenterCalibration:
    def __init__(self, config):
        
        self.camera_width = 1920
        self.camera_height = 1080

        # roi ratios
        x_p = 0.15
        y_p = 0.15
        w_p = 0.45
        h_p = 0.7

        fx = 1325.818723
        fy = 1325.818723
        cx = 960.000000
        cy = 540.000000
        k1 = -0.464634
        k2 = 0.233666
        p1 = 0.003345
        p2 = -0.006846
        k3 = 0.0

        self.camera_matrix = np.array([
            [fx, 0, cx],
            [0, fy, cy],
            [0, 0, 1]
        ], dtype=np.float32)
        self.dist_coeffs = np.array([k1, k2, p1, p2, k3], dtype=np.float32)

        self.camera_roi = (int(self.camera_width*x_p), int(self.camera_height*y_p), int(self.camera_width*w_p), int(self.camera_height*h_p))  # x, y, w, h

        np_camera_points = np.array(known_camera_points_px, dtype=np.float32)
        np_printer_points = np.array(known_printer_points_mm, dtype=np.float32)

        self.h_matrix, _ = cv2.findHomography(np_camera_points, np_printer_points)

        available_cameras = []
        max_devices = 5
        self.resize_percent = 40  # for camera display window

        #
        self.param1 = 50
        self.param2 = 25
        self.min_radius = 200
        self.max_radius = 250

        # for moving average
        self.moving_avg_center = None
        self.alpha = 0.2

        # blur filter size
        self.k_size = 5
        self.sig_x = 0

        for i in range(max_devices):
            device_path = f"/dev/video{i}"
            if os.path.exists(device_path):
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    available_cameras.append(i)
                    cap.release()
        
        self.available_cameras = available_cameras
        logging.info(f"Available cameras for bed center calibration: {self.available_cameras}")

    def get_camera_roi(self):
        return self.camera_roi

    def get_camera_size(self):
        return self.camera_width, self.camera_height

    def get_available_cameras(self):
        return self.available_cameras
    
    def get_h_matrix(self):
        return self.h_matrix
    
    def calc_calib_coord(self, camshow = False):
        logging.info("Starting bed center calibration...")
        try:
            cap = cv2.VideoCapture(self.get_available_cameras()[0])
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.get_camera_size()[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.get_camera_size()[1])
            logging.info(f"cam size: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")

            # cap.set(cv2.CAP_PROP_BRIGHTNESS, 0) 
            # cap.set(cv2.CAP_PROP_CONTRAST, 45)

            # cap.set(cv2.CAP_PROP_AUTO_WB, 1)
            # cap.set(cv2.CAP_PROP_WB_TEMPERATURE, 6000) 

            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)    # 자동 노출 비활성화

            cap.set(cv2.CAP_PROP_EXPOSURE, 20)        # 예시 값

        except Exception as e:
            logging.error(f"Failed to open camera: {e}")
            return "Failed to open camera"
        
        self.calib_coord = None

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
            self.calib_coord = cv2.perspectiveTransform(np.array([pixel_coord]), self.get_h_matrix())

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