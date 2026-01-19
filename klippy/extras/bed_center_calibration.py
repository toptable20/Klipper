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
known_printer_points_mm = [
    (175, 20), (100, 20), (30, 20),
    (175, 100), (100, 100), (30, 100),
    (175, 175), (100, 175), (30, 175)
]

known_camera_points_px = [
    (640.5, 257.5), (865.5, 262.5), (1080.5, 262.5),
    (637.5, 505.5), (864.5, 510.5), (1080.5, 512.5),
    (637.5, 732.5), (864.5, 737.5), (1077.5, 743.5)
]

# 3.0 new cam
# known_printer_points_mm = [
#     (175, 20), (100, 20), (30, 20),
#     (175, 100), (100, 100), (30, 100),
#     (175, 175), (100, 175), (30, 175)
# ]

# known_camera_points_px = [
#     (472.5, 268.5), (725.5, 265.5), (966.5, 262.5),
#     (476.5, 544.5), (731.5, 540.5), (969.5, 538.5),
#     (479.5, 798.5), (732.5, 800.5), (970.5, 796.5)
# ]

# 3.0 
# known_printer_points_mm = [
#     (175, 20), (100, 20), (30, 20),
#     (175, 100), (100, 100), (30, 100),
#     (175, 175), (100, 175), (30, 175)
# ]

# known_camera_points_px = [
#     (519.5, 305.5), (746.5, 302.5), (961.5, 300.5),
#     (524.5, 550.5), (750.5, 550.5), (965.5, 545.5),
#     (530.5, 780.5), (757.5, 779.5), (970.5, 777.5)
# ]

class BedCenterCalibration:
    def __init__(self, config):
        
        self.camera_width = 1920
        self.camera_height = 1080

        # self.camera_width = 1280
        # self.camera_height = 720

        # roi ratios
        x_p = 0.15
        y_p = 0.15
        w_p = 0.45
        h_p = 0.7
        # x_p = 0.25
        # y_p = 0.05
        # w_p = 0.45
        # h_p = 0.60

        fx = 2059.877690
        fy = 2059.877690
        cx = 960.000000
        cy = 540.000000
        k1 = -1.120172
        k2 = 1.932153
        p1 = -0.012699
        p2 = 0.030329
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

        # self.h_matrix, _ = cv2.findHomography(np_camera_points, np_printer_points)
        # h_list = [[ 3.73458970e-01, -1.75049397e-02, -1.09760592e+02],
        #             [-9.36206623e-03, -3.85290494e-01,  2.10532741e+02],
        #             [-3.94690392e-05, -9.11647266e-05,  1.00000000e+00]]
        h_list = [[-4.09314335e-01,  4.88190012e-03,  4.60610128e+02],
                    [ 7.90331266e-03,  4.06450762e-01, -1.05692373e+02],
                    [ 1.85405618e-05, -1.95641987e-05,  1.00000000e+00]]
        self.h_matrix = np.array(h_list, dtype=np.float64)

        available_cameras = []
        max_devices = 5
        self.resize_percent = 40  # for camera display window

        #
        self.param1 = 50
        self.param2 = 25
        self.min_radius = 200
        self.max_radius = 250

        # for moving average
        self.moving_avg_center = [None] * 20
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

            cap.set(cv2.CAP_PROP_EXPOSURE, 1500)        # 예시 값

        except Exception as e:
            logging.error(f"Failed to open camera: {e}")
            return "Failed to open camera"
        
        self.calib_coord = None

        logging.info("Start capturing images")
        fail_count = 0
        success_count = 0
        # self.moving_avg_center = None
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

            printbed_roi = self.get_camera_roi()

            # hough circle detection
            # img_undistorted = cv2.undistort(frame, self.camera_matrix, self.dist_coeffs, None, self.camera_matrix)
            # img_roi = img_undistorted[printbed_roi[1]:printbed_roi[1]+printbed_roi[3], printbed_roi[0]:printbed_roi[0]+printbed_roi[2]]

            # logging.info("Copying frame")
            # frame2 = img_undistorted.copy()
            # img_roi = cv2.GaussianBlur(img_roi, (self.k_size, self.k_size), self.sig_x)
            # gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
            # circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, 10000, param1 = self.param1, param2 = self.param2, minRadius = self.min_radius, maxRadius = self.max_radius)
            # cv2.rectangle(frame2, (printbed_roi[0], printbed_roi[1]), (printbed_roi[0]+printbed_roi[2], printbed_roi[1]+printbed_roi[3]), 255, 1)
        
            # if circles is None or circles.shape[-1] != 3:
            #     logging.warning("No circles detected, retrying...")

            #     img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_fail_{fail_count}.png"
            #     cv2.imwrite(img_filename, frame2)
            #     logging.info(f"Saved image: {img_filename}")

            #     fail_count += 1
            #     continue

            # logging.info(f"Circles detected {circles}")
            # cx = cy = 0
            # for i in circles[0]:
            #     cx = i[0] + printbed_roi[0]   # ROI의 x offset 추가
            #     cy = i[1] + printbed_roi[1]   # ROI의 y offset 추가
            #     cv2.circle(frame2, (int(cx), int(cy)), int(i[2]), (0,0,255), 2)  #원 그리기
            #     cv2.circle(frame2, (int(cx), int(cy)), 2, (0,0,255), 3)  #원 그리기

            # logging.info(f"Circle center: ({cx}, {cy})")


            # HLS contour detection
            # img_roi = frame[printbed_roi[1]:printbed_roi[1]+printbed_roi[3], 
            #         printbed_roi[0]:printbed_roi[0]+printbed_roi[2]]
            # img_color = cv2.cvtColor(img_roi, cv2.COLOR_BGR2HLS)
            # hx, lx, sx = cv2.split(img_color)
            # image_color_band = sx
            # _, binary_image = cv2.threshold(image_color_band, 80, 200, cv2.THRESH_BINARY)
            # kernel = np.ones((5,5), np.uint8)
            # binary_image = cv2.morphologyEx(binary_image, cv2.MORPH_CLOSE, kernel)
            # contours, _ = cv2.findContours(binary_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # target_centers = []
            # for cnt in contours:
            #     area = cv2.contourArea(cnt)
            #     if area < 500:
            #         continue

            #     M = cv2.moments(cnt)
            #     if M['m00'] == 0:
            #         continue

            #     mc_x = M["m10"] / M["m00"]
            #     mc_y = M["m01"] / M["m00"]

            #     epsilon = 0.005 * cv2.arcLength(cnt, True)
            #     approx = cv2.approxPolyDP(cnt, epsilon, True)
            #     # approx = cv2.approxPolyDP(cnt, 10, True)
            #     bound_rect = cv2.boundingRect(approx)

            #     # print("bound_rect:", bound_rect)

            #     if bound_rect[2] < 5 or bound_rect[3] < 50:  # width, height
            #         continue

            #     (x, y, w, h) = bound_rect

            #     cv2.rectangle(frame, (printbed_roi[0] + x, printbed_roi[1] + y), (printbed_roi[0] + x + w, printbed_roi[1] + y + h), (0, 0, 255), 2)

            #     color = (0, 0, 255)
            #     offset = 5

            #     abs_x = mc_x + (printbed_roi[0] if printbed_roi else 0)
            #     abs_y = mc_y + (printbed_roi[1] if printbed_roi else 0)

            #     cv2.drawContours(frame, [approx + np.array([ [printbed_roi[0], printbed_roi[1]] ])], 0, (0,255,0), 2)

            #     cv2.circle(frame, (int(abs_x), int(abs_y)), 3, color, -1, 8, 0)    # 중심점 그리기

                
            #     # print("center: ", abs_x, abs_y)
            #     # print("Bounding Rect:", x, y, w, h)
            #     cv2.rectangle(
            #         frame,
            #         (int(x + (printbed_roi[0] if printbed_roi else 0) - offset),
            #         int(y + (printbed_roi[1] if printbed_roi else 0)- offset)),
            #         (int(x + w + (printbed_roi[0] if printbed_roi else 0) + offset),
            #         int(y + h + (printbed_roi[1] if printbed_roi else 0) + offset)),
            #         color, 1
            #     )

            #     target_centers.append((abs_x, abs_y))



            img_undistorted = cv2.undistort(frame, self.camera_matrix, self.dist_coeffs, None, self.camera_matrix)

            # Canny contour detection
            img_roi = img_undistorted[printbed_roi[1]:printbed_roi[1]+printbed_roi[3], 
                            printbed_roi[0]:printbed_roi[0]+printbed_roi[2]]
            
            # 1. 전처리 (그레이스케일 + 블러)
            gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)

            # 2. Canny Edge 적용
            # 변수: 100(낮은 임계값), 200(높은 임계값) -> 이 수치를 조절하는 것이 핵심입니다.
            edges = cv2.Canny(blurred, 0, 95)

            # 3. 엣지 연결 (모폴로지 연산)
            # 엣지가 끊어져 있으면 컨투어가 제대로 안 따지므로 선을 살짝 두껍게 만듭니다.
            kernel = np.ones((3,3), np.uint8)
            # kernel = None
            edges = cv2.dilate(edges, kernel, iterations=5) # 팽창
            edges = cv2.erode(edges, kernel, iterations=5)  # 수축 (끊어진 선 연결 후 복구)

            # 4. 컨투어 추출 (Canny 결과물은 이미 이진화된 상태와 같음)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            target_centers = []
            contours = sorted(contours, key=lambda c: cv2.boundingRect(c)[0])
            cv2.rectangle(img_undistorted, (printbed_roi[0], printbed_roi[1]), (printbed_roi[0]+printbed_roi[2], printbed_roi[1]+printbed_roi[3]), (255,0,0), 1)

            for cnt in contours:
                area = cv2.contourArea(cnt)
                logging.info(f"Contour area: {area}")
                if area < 10000 or area > 50000:
                    continue

                M = cv2.moments(cnt)
                if M['m00'] == 0:
                    continue

                mc_x = M["m10"] / M["m00"]
                mc_y = M["m01"] / M["m00"]

                epsilon = 0.005 * cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, epsilon, True)
                # approx = cv2.approxPolyDP(cnt, 10, True)
                bound_rect = cv2.boundingRect(approx)

                # print("bound_rect:", bound_rect)

                if bound_rect[2] < 5 or bound_rect[3] < 50:  # width, height
                    continue

                (x, y, w, h) = bound_rect

                cv2.rectangle(img_undistorted, (printbed_roi[0] + x, printbed_roi[1] + y), (printbed_roi[0] + x + w, printbed_roi[1] + y + h), (0, 0, 255), 2)

                color = (0, 0, 255)
                offset = 5

                abs_x = mc_x + (printbed_roi[0] if printbed_roi else 0)
                abs_y = mc_y + (printbed_roi[1] if printbed_roi else 0)

                cv2.drawContours(img_undistorted, [approx + np.array([ [printbed_roi[0], printbed_roi[1]] ])], 0, (0,255,0), 2)

                cv2.circle(img_undistorted, (int(abs_x), int(abs_y)), 3, color, -1, 8, 0)    # 중심점 그리기

                
                print("center: ", abs_x, abs_y)
                # print("Bounding Rect:", x, y, w, h)
                cv2.rectangle(
                    img_undistorted,
                    (int(x + (printbed_roi[0] if printbed_roi else 0) - offset),
                    int(y + (printbed_roi[1] if printbed_roi else 0)- offset)),
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
                        # 그룹의 현재 평균값(이동평균) 계산
                        avg_x = sum(p[0] for p in group) / len(group)
                        avg_y = sum(p[1] for p in group) / len(group)
                        
                        dist = ((pt[0] - avg_x)**2 + (pt[1] - avg_y)**2)**0.5
                        if dist < threshold:
                            group.append(pt)
                            matched = True
                            break
                    
                    if not matched:
                        # 새로운 대상이 발견됨
                        detected_groups.append([pt])
                logging.info(f"Frame {success_count} processed. Groups: {len(detected_groups)}")
            else:
                logging.warning("No targets detected in this frame.")
                fail_count += 1

            final_calib_coords = []
            for group in detected_groups:
                if len(group) >= 3: # 최소 3회 이상 인식된 그룹만 신뢰 (노이즈 제거)
                    avg_px = sum(p[0] for p in group) / len(group)
                    avg_py = sum(p[1] for p in group) / len(group)
                    
                    # 여기서 h_matrix를 사용하여 최종 프린터 좌표(mm)로 변환
                    # target_mm = self.apply_homography(avg_px, avg_py) 
                    final_calib_coords.append((avg_px, avg_py)) # 우선 픽셀로 저장
            
            logging.info(f"Detected target centers: {final_calib_coords}")
            if try_count >= 3:
                if len(final_calib_coords) == 0:
                    logging.warning("No circles detected, retrying...")

                    img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_fail_{fail_count}.png"
                    cv2.imwrite(img_filename, img_undistorted)
                    logging.info(f"Saved image: {img_filename}")

                    fail_count += 1
                    continue

                # if self.moving_avg_center is None or len(final_calib_coords) > len(self.moving_avg_center):
                #     num_targets = len(target_centers)
                #     self.moving_avg_center = [None] * num_targets
                #     logging.info(f"Initialized moving avg for {num_targets} targets")

                img_filename = f"/home/mks/printer_data/screenshot/undistorted_image_success_{success_count}.png"
                cv2.imwrite(img_filename, img_undistorted)
                logging.info(f"Saved image: {img_filename}")
                success_count += 1

                # pixel_coord = [None for _ in range(len(target_centers))]
                # self.calib_coord = [None for _ in range(len(final_calib_coords))]
                
                target_idx = 0
                for center in final_calib_coords:
                    cx, cy = center
                    # pixel_coord[target_idx] = np.array([[cx, cy]])
                    # self.calib_coord[target_idx] = cv2.perspectiveTransform(np.array([pixel_coord[target_idx]]), self.get_h_matrix())
                    # self.moving_avg_center[target_idx] = (self.alpha * self.calib_coord[target_idx][0][0]) + (1 - self.alpha) * (self.moving_avg_center[target_idx] if self.moving_avg_center[target_idx] is not None else self.calib_coord[target_idx][0][0])
                    pixel_pt = np.array([[[cx, cy]]], dtype=np.float32)
                    transformed_pt = cv2.perspectiveTransform(pixel_pt, self.get_h_matrix())
                    current_coord = transformed_pt[0][0]

                    if self.moving_avg_center[target_idx] is None:
                        new_avg = current_coord
                    else:
                        new_avg = (self.alpha * current_coord) + (1 - self.alpha) * self.moving_avg_center[target_idx]

                    self.moving_avg_center[target_idx] = new_avg
                    logging.info(f"calculated pos: {self.moving_avg_center[target_idx]}")
                    target_idx += 1
                    
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
        
        if self.moving_avg_center is not None:
            final_list = []
            for item in self.moving_avg_center:
                if item is not None:
                    # item이 Numpy array이든 리스트이든 상관없이 [x, y]로 변환
                    val = item.tolist() if hasattr(item, 'tolist') else item
                    # 만약 val이 [[x, y]] 처럼 감싸져 있다면 벗겨냄
                    while isinstance(val, list) and len(val) == 1 and isinstance(val[0], list):
                        val = val[0]
                    final_list.append(val)
            
            logging.info(f"Final Clean Coords: {final_list}") # 로그에 [[x, y], [x, y]]로 찍혀야 함
            return final_list

        return self.moving_avg_center
    
def load_config(config):
    return BedCenterCalibration(config)