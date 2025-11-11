import os
import cv2
import numpy as np

import logging

# Known calibration points for bed center calibration
known_printer_points_mm = [
    (130, 140), (95, 140), (130.50, 177),
    (112, 160), (149, 160), (167, 141),
    (151, 125), (130.5, 105), (115, 122)
]

known_camera_points_px = [
    (323.5, 180.5), (228.5, 183.5), (322.5, 80.5),
    (273.5, 129.5), (373.5, 125.5), (422.5, 175.5),
    (382.5, 223.5), (330.5, 279.5), (287.5, 233.5)
]

class BedCenterCalibration:
    def __init__(self, config):
        
        self.camera_width = 640
        self.camera_height = 360
        self.camera_roi = (95, 30, 450, 300)  # x, y, w, h

        np_camera_points = np.array(known_camera_points_px, dtype=np.float32)
        np_printer_points = np.array(known_printer_points_mm, dtype=np.float32)

        self.h_matrix, _ = cv2.findHomography(np_camera_points, np_printer_points)

        available_cameras = []
        max_devices = 5

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
            logging.info(f"cam size: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.get_camera_size()[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.get_camera_size()[1])
        except Exception as e:
            logging.error(f"Failed to open camera: {e}")
            return "Failed to open camera"
        
        self.calib_coord = None

        logging.info("Start capturing images")
        fail_count = 0
        while fail_count < 10 and cap.isOpened():
            ret, frame = cap.read()
            logging.info("Captured image")
            if not ret:
                logging.warning("Failed to capture image from camera.")
                fail_count += 1
                continue
            printbed_roi = self.get_camera_roi()
            img_roi = frame[printbed_roi[1]:printbed_roi[1]+printbed_roi[3], printbed_roi[0]:printbed_roi[0]+printbed_roi[2]]

            logging.info("Copying frame")
            frame2 = frame.copy()
            gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
            circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, 100, param1 = 150, param2 = 50, minRadius = 5, maxRadius = 70)
            cv2.rectangle(frame2, (printbed_roi[0], printbed_roi[1]), (printbed_roi[0]+printbed_roi[2], printbed_roi[1]+printbed_roi[3]), 255, 1)

            if camshow:
                cv2.imshow('frame', frame2)

            if circles is None:
                logging.warning("No circles detected, retrying...")
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

            pixel_coord = np.array([[cx, cy]], dtype=np.float32)
            self.calib_coord = cv2.perspectiveTransform(np.array([pixel_coord]), self.get_h_matrix())

            logging.info(f"calculated pos: {self.calib_coord}")
            if camshow:
                cv2.imshow('frame', frame2)

            break          
        
        cap.release()

        if fail_count >= 10:
            logging.error("Bed center calibration failed after multiple attempts.")
            return "Failed to detect circle"

        return self.calib_coord
    
def load_config(config):
    return BedCenterCalibration(config)