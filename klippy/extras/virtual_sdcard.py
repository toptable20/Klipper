# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io
import re
import datetime
import time

VALID_GCODE_EXTS = ['gcode', 'g', 'gco']

DEFAULT_ERROR_GCODE = """
{% if 'heaters' in printer %}
   TURN_OFF_HEATERS
{% endif %}
"""

class VirtualSD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:shutdown",
                                            self.handle_shutdown)
        # sdcard state
        sd = config.get('path')
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        self.current_file = None
        self.file_position = self.file_size = 0
        # Print Stat Tracking
        self.print_stats = self.printer.load_object(config, 'print_stats')
        # Work timer
        self.reactor = self.printer.get_reactor()
        self.must_pause_work = self.cmd_from_sd = False
        self.next_file_position = 0
        self.work_timer = None
        # Error handling
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.on_error_gcode = gcode_macro.load_template(
            config, 'on_error_gcode', DEFAULT_ERROR_GCODE)
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        for cmd in ['M20', 'M21', 'M23', 'M24', 'M25', 'M26', 'M27']:
            self.gcode.register_command(cmd, getattr(self, 'cmd_' + cmd))
        for cmd in ['M28', 'M29', 'M30']:
            self.gcode.register_command(cmd, self.cmd_error)
        self.gcode.register_command(
            "SDCARD_RESET_FILE", self.cmd_SDCARD_RESET_FILE,
            desc=self.cmd_SDCARD_RESET_FILE_help)
        self.gcode.register_command(
            "SDCARD_PRINT_FILE", self.cmd_SDCARD_PRINT_FILE,
            desc=self.cmd_SDCARD_PRINT_FILE_help)
        
        self.calc_print_time = self.printer.load_object(config, 'print_time_calc')

        self.bed_center_calibration = self.printer.load_object(config, 'bed_center_calibration')
        self.file_name = None
        self.gcode_move = self.printer.load_object(config, 'gcode_move')

    def handle_shutdown(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.current_file.seek(readpos)
                data = self.current_file.read(readcount + 128)
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info("Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                         readpos, repr(data[:readcount]),
                         self.file_position, repr(data[readcount:]))
    def stats(self, eventtime):
        if self.work_timer is None:
            return False, ""
        return True, "sd_pos=%d" % (self.file_position,)
    def get_file_list(self, check_subdirs=False):
        if check_subdirs:
            flist = []
            for root, dirs, files in os.walk(
                    self.sdcard_dirname, followlinks=True):
                for name in files:
                    ext = name[name.rfind('.')+1:]
                    if ext not in VALID_GCODE_EXTS:
                        continue
                    full_path = os.path.join(root, name)
                    r_path = full_path[len(self.sdcard_dirname) + 1:]
                    size = os.path.getsize(full_path)
                    flist.append((r_path, size))
            return sorted(flist, key=lambda f: f[0].lower())
        else:
            dname = self.sdcard_dirname
            try:
                filenames = os.listdir(self.sdcard_dirname)
                return [(fname, os.path.getsize(os.path.join(dname, fname)))
                        for fname in sorted(filenames, key=str.lower)
                        if not fname.startswith('.')
                        and os.path.isfile((os.path.join(dname, fname)))]
            except:
                logging.exception("virtual_sdcard get_file_list")
                raise self.gcode.error("Unable to get file list")
    def get_status(self, eventtime=None):
        return {
            'file_path': self.file_path(),
            'progress': self.progress(),
            'is_active': self.is_active(),
            'file_position': self.file_position,
            'file_size': self.file_size,
        }
    def file_path(self):
        if self.current_file:
            return self.current_file.name
        return None
    def progress(self):
        if self.file_size:
            return float(self.file_position) / self.file_size
        else:
            return 0.
    def is_active(self):
        return self.work_timer is not None
    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + .001)
    def do_resume(self):
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        self.must_pause_work = False
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW)
    def do_cancel(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
            self.print_stats.note_cancel()
        self.file_position = self.file_size = 0
    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error("SD write not supported")
    def _reset_file(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
        self.file_position = self.file_size = 0
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")
    cmd_SDCARD_RESET_FILE_help = "Clears a loaded SD File. Stops the print "\
        "if necessary"
    def cmd_SDCARD_RESET_FILE(self, gcmd):
        if self.cmd_from_sd:
            raise gcmd.error(
                "SDCARD_RESET_FILE cannot be run from the sdcard")
        self._reset_file()
    cmd_SDCARD_PRINT_FILE_help = "Loads a SD file and starts the print.  May "\
        "include files in subdirectories."
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        logging.info("SDCARD_PRINT_FILE command received")
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        
        self._reset_file()
        filename = gcmd.get("FILENAME")
        self.file_name = filename
        
        # if self.print_stats.get_bed_center_calibration():
        #     logging.info("Bed center calibration required before print start")

        #     # mutex lock to prevent gcode command conflict
        #     self.gcode.get_mutex().__exit__()
            
        #     # move to capture point
        #     self.gcode._process_commands("G28".split("\n"), need_ack=False)
        #     while self.printer.lookup_object('toolhead').is_busy():
        #         self.reactor.pause(self.reactor.monotonic() + 0.1)
            
        #     self.gcode._process_commands("G1 X-25 Y100 Z90 F3000".split("\n")   , need_ack=False)
        #     while self.printer.lookup_object('toolhead').is_busy():
        #         self.reactor.pause(self.reactor.monotonic() + 0.1)
            
        #     logging.info("Positioned for bed center calibration")

        #     while True:
        #         if self.gcode.get_mutex():
        #             logging.info("Gcode mutex locked for bed center calibration")
        #             time.sleep(0.5)
        #         else:
        #             break

        #     calib_coord = self.bed_center_calibration.calc_calib_coord()
        #     if calib_coord is None:
        #         raise gcmd.error("Bed center calibration failed")
        #     elif "Failed" in calib_coord:
        #         raise gcmd.error(calib_coord)

        #     logging.info(f"Calibrated coord: {calib_coord}")
            
        #     pattern_x = re.compile(r"X([-+]?\d*\.?\d+)")
        #     pattern_y = re.compile(r"Y([-+]?\d*\.?\d+)")

        #     inputfile = os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", filename)
        #     base, ext = os.path.splitext(filename)
        #     outputfilename = f"{base}_calib{ext}"
            
        #     if not os.path.exists(os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", "calib")):
        #         os.makedirs(os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", "calib"))
        #     outputfile = os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", "calib", outputfilename)

        #     modified_lines = []
        #     printer_center = 102.5, 102.5
        #     with open(inputfile, "r", encoding="utf-8") as f:
        #         first_line = f.readline()
        #         if first_line.startswith('; calibrated data by bed center calibration'):
        #             logging.info("Already calibrated file detected. Aborting to prevent double calibration.")
        #             os.remove(outputfile)

        #         else:
        #             lines = f.readlines()
                        
        #             for line in lines:
        #                 if line.startswith(("G0", "G1")):
        #                     new_line = line

        #                     match_x = pattern_x.search(line)
        #                     if match_x:
        #                         x_val = float(match_x.group(1))
        #                         new_x = calib_coord[0][0][0] - printer_center[0] + x_val
        #                         new_line = pattern_x.sub(f"X{new_x:.3f}", new_line)

        #                     match_y = pattern_y.search(line)
        #                     if match_y:
        #                         y_val = float(match_y.group(1))
        #                         new_y = calib_coord[0][0][1] - printer_center[1] + y_val
        #                         new_line = pattern_y.sub(f"Y{new_y:.3f}", new_line)

        #                     modified_lines.append(new_line)
        #                 else:
        #                     modified_lines.append(line)
                    
        #             with open(outputfile, "w", encoding="utf-8") as f:
        #                 new_first_line = f'; calibrated data by bed center calibration ({datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})\n'
        #                 modified_lines = [new_first_line] + modified_lines
        #                 f.writelines(modified_lines)

        #             filename = outputfilename
        
        # logging.info(f"Loading file: {filename}")

        # # back to home position before print start
        # self.gcode.run_script("G1 X-100 Y205 Z10 F3000")

        if filename[0] == '/':
            filename = filename[1:]
        self._load_file(gcmd, filename, check_subdirs=True)
        self.do_resume()
    def cmd_M20(self, gcmd):
        # List SD card
        files = self.get_file_list()
        gcmd.respond_raw("Begin file list")
        for fname, fsize in files:
            gcmd.respond_raw("%s %d" % (fname, fsize))
        gcmd.respond_raw("End file list")
    def cmd_M21(self, gcmd):
        # Initialize SD card
        gcmd.respond_raw("SD card ok")
    def cmd_M23(self, gcmd):
        logging.info("M23 command received")
        # Select SD file
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get_raw_command_parameters().strip()
        if filename.startswith('/'):
            filename = filename[1:]
        self._load_file(gcmd, filename)
    def _load_file(self, gcmd, filename, check_subdirs=False):
        files = self.get_file_list(check_subdirs)
        flist = [f[0] for f in files]
        files_by_lower = { fname.lower(): fname for fname, fsize in files }
        fname = filename
        try:
            if fname not in flist:
                fname_lower = fname.lower()
                if fname_lower in files_by_lower:
                    fname = files_by_lower[fname_lower]
                else:
                    candidates = [f for f in flist if f.lower().endswith('/' + fname_lower) or f.lower() == fname_lower]
                    if candidates:
                        fname = candidates[0]
                    else:
                        raise gcmd.error("File not found")
                
            fname = os.path.join(self.sdcard_dirname, fname)
            f = io.open(fname, 'r', newline='')
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise gcmd.error("Unable to open file")
        gcmd.respond_raw("File opened:%s Size:%d" % (filename, fsize))
        gcmd.respond_raw("File selected")
        self.current_file = f
        self.file_position = 0
        self.file_size = fsize
        self.print_stats.set_current_file(filename)
    def cmd_M24(self, gcmd):
        # Start/resume SD print
        self.do_resume()
    def cmd_M25(self, gcmd):
        # Pause SD print
        self.do_pause()
    def cmd_M26(self, gcmd):
        # Set SD position
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        pos = gcmd.get_int('S', minval=0)
        self.file_position = pos
    def cmd_M27(self, gcmd):
        # Report SD print status
        if self.current_file is None:
            gcmd.respond_raw("Not SD printing.")
            return
        gcmd.respond_raw("SD printing byte %d/%d"
                         % (self.file_position, self.file_size))
    def get_file_position(self):
        return self.next_file_position
    def set_file_position(self, pos):
        self.next_file_position = pos
    def is_cmd_from_sd(self):
        return self.cmd_from_sd
    # Background work timer
    def work_handler(self, eventtime):
        error_message = None

        if self.print_stats.get_bed_center_calibration():
            logging.info("Bed center calibration required before print start")

            # mutex lock to prevent gcode command conflict
            # self.gcode.get_mutex().__exit__()
            
            # move to capture point
            self.gcode._process_commands("G28\nG91\nG1 E-50\nG90\nG1 X-25 Y100 Z95 F3000\n".split("\n"), need_ack=True)
            if self.gcode.get_mutex():
                logging.info("waiting for gcode mutex to release for bed center calibration2")
                self.reactor.pause(self.reactor.monotonic() + 15.0)

            logging.info("Positioned for bed center calibration")

            calib_coord = self.bed_center_calibration.calc_calib_coord()
            if calib_coord is None:
                error_message = "Bed center calibration failed"
                # raise gcmd.error("Bed center calibration failed")
            elif "Failed" in calib_coord:
                error_message = calib_coord
                # raise gcmd.error(calib_coord)

            logging.info(f"Calibrated coord: {calib_coord}")
            logging.info(f"calib_coord[0]: {calib_coord[0]}, calib_coord[1]: {calib_coord[1]}")

            if error_message is not None:
                self.work_timer = None
                self.gcode.respond_raw(f"Error: {error_message}")
                return self.reactor.NEVER
            
            pattern_x = re.compile(r"X([-+]?\d*\.?\d+)")
            pattern_y = re.compile(r"Y([-+]?\d*\.?\d+)")

            inputfile = os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", self.file_name)
            base, ext = os.path.splitext(self.file_name)
            outputfilename = f"{base}_calib{ext}"
            
            if not os.path.exists(os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", "calib")):
                os.makedirs(os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", "calib"))
            outputfile = os.path.join(os.path.expanduser("~/"), "printer_data", "gcodes", "calib", outputfilename)

            modified_lines = []
            printer_center = 102.5, 102.5
            with open(inputfile, "r", encoding="utf-8") as f:
                first_line = f.readline()
                if first_line.startswith('; calibrated data by bed center calibration'):
                    logging.info("Already calibrated file detected. Aborting to prevent double calibration.")
                    os.remove(outputfile)

                else:
                    lines = f.readlines()
                        
                    for line in lines:
                        if line.startswith(("G0", "G1")):
                            new_line = line

                            match_x = pattern_x.search(line)
                            if match_x:
                                x_val = float(match_x.group(1))
                                new_x = calib_coord[0] - printer_center[0] + x_val
                                new_line = pattern_x.sub(f"X{new_x:.3f}", new_line)

                            match_y = pattern_y.search(line)
                            if match_y:
                                y_val = float(match_y.group(1))
                                new_y = calib_coord[1] - printer_center[1] + y_val
                                new_line = pattern_y.sub(f"Y{new_y:.3f}", new_line)

                            modified_lines.append(new_line)
                        else:
                            modified_lines.append(line)
                    
                    with open(outputfile, "w", encoding="utf-8") as f:
                        new_first_line = f'; calibrated data by bed center calibration ({datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})\n'
                        modified_lines = [new_first_line] + modified_lines
                        f.writelines(modified_lines)

                    filename = outputfilename

                    self._load_file(self.gcode, filename, check_subdirs=True)
        
                    logging.info(f"Loading file: {filename}")

            # back to home position before print start
            self.gcode.run_script("G1 X-100 Y205 Z10 E0 F3000")

        logging.info("Starting SD card print (position %d)", self.file_position)
        self.reactor.unregister_timer(self.work_timer)
        try:
            self.current_file.seek(self.file_position)
        except:
            logging.exception("virtual_sdcard seek")
            self.work_timer = None
            return self.reactor.NEVER
        self.print_stats.note_start()
        gcode_mutex = self.gcode.get_mutex()
        partial_input = ""
        lines = []
        
        while not self.must_pause_work:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                except:
                    logging.exception("virtual_sdcard read")
                    break
                if not data:
                    # End of file
                    self.current_file.close()
                    self.current_file = None
                    logging.info("Finished SD card print")
                    self.gcode.respond_raw("Done printing file")
                    break
                lines = data.split('\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            # Pause if any other request is pending in the gcode class
            if gcode_mutex.test():
                self.reactor.pause(self.reactor.monotonic() + 0.100)
                continue
            # Dispatch command
            self.cmd_from_sd = True
            line = lines.pop()
            if sys.version_info.major >= 3:
                next_file_position = self.file_position + len(line.encode()) + 1
            else:
                next_file_position = self.file_position + len(line) + 1
            self.next_file_position = next_file_position
            try:
                self.gcode.run_script(line)
            except self.gcode.error as e:
                error_message = str(e)
                try:
                    self.gcode.run_script(self.on_error_gcode.render())
                except:
                    logging.exception("virtual_sdcard on_error")
                break
            except:
                logging.exception("virtual_sdcard dispatch")
                break
            self.cmd_from_sd = False
            self.file_position = self.next_file_position
            # Do we need to skip around?
            if self.next_file_position != next_file_position:
                try:
                    self.current_file.seek(self.file_position)
                except:
                    logging.exception("virtual_sdcard seek")
                    self.work_timer = None
                    return self.reactor.NEVER
                lines = []
                partial_input = ""
        logging.info("Exiting SD card print (position %d)", self.file_position)
        self.work_timer = None
        self.cmd_from_sd = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
        elif self.current_file is not None:
            self.print_stats.note_pause()
        else:
            self.print_stats.note_complete()
        return self.reactor.NEVER

def load_config(config):
    return VirtualSD(config)
