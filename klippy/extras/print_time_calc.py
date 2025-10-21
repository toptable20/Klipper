import re
import math
import datetime
from array import array

import logging

gcode_parsed_args = ["x", "y", "e", "f", "z", "i", "j"]
gcode_parsed_nonargs = 'gtmnd'
to_parse = "".join(gcode_parsed_args) + gcode_parsed_nonargs
gcode_exp = re.compile("\([^\(\)]*\)|;.*|[/\*].*\n|([%s])\s*([-+]?[0-9]*\.?[0-9]*)" % to_parse)
specific_exp = "(?:\([^\(\)]*\))|(?:;.*)|(?:[/\*].*\n)|(%s[-+]?[0-9]*\.?[0-9]*)"
move_gcodes = ["G0", "G1", "G2", "G3"]

class Layer(list):

    __slots__ = ("duration", "z")

    def __init__(self, lines, z = None):
        super(Layer, self).__init__(lines)
        self.z = z
        self.duration = 0

class PyLine:

    __slots__ = ('x', 'y', 'z', 'e', 'f', 'i', 'j',
                 'raw', 'command', 'is_move',
                 'relative', 'relative_e',
                 'current_x', 'current_y', 'current_z', 'extruding',
                 'current_tool',
                 'gcview_end_vertex')

    def __init__(self, l):
        self.raw = l

    def __getattr__(self, name):
        return None

class PyLightLine:

    __slots__ = ('raw', 'command')

    def __init__(self, l):
        self.raw = l

    def __getattr__(self, name):
        return None

try:
    from . import gcoder_line
    Line = gcoder_line.GLine
    LightLine = gcoder_line.GLightLine
except Exception as e:
    Line = PyLine
    LightLine = PyLightLine

def find_specific_code(line, code):
    exp = specific_exp % code
    bits = [bit for bit in re.findall(exp, line.raw) if bit]
    if not bits: return None
    else: return float(bits[0][1:])

def S(line):
    return find_specific_code(line, "S")

def P(line):
    return find_specific_code(line, "P")

def split(line):
    split_raw = gcode_exp.findall(line.raw.lower())
    if split_raw and split_raw[0][0] == "n":
        del split_raw[0]
    if not split_raw:
        line.command = line.raw
        line.is_move = False
        logging.warning("raw G-Code line \"%s\" could not be parsed" % line.raw)
        return [line.raw]
    command = split_raw[0]
    line.command = command[0].upper() + command[1]
    line.is_move = line.command in move_gcodes
    return split_raw


def parse_coordinates(line, split_raw, imperial = False, force = False):
    # Not a G-line, we don't want to parse its arguments
    if not force and line.command[0] != "G":
        return
    unit_factor = 25.4 if imperial else 1
    for bit in split_raw:
        code = bit[0]
        if code not in gcode_parsed_nonargs and bit[1]:
            setattr(line, code, unit_factor * float(bit[1]))

class CalcPrintTime:

    def __init__(self, config):
        self.printer = config.get_printer()
        self.total_time = 0.0
        
        # self.virtual_sd = self.printer.load_object(config, 'virtual_sdcard')

        self.filepath = None
        self.line_class = Line

        self.imperial = False
        self.relative = False
        self.relative_e = False
        self.current_tool = 0
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_z = 0.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.offset_z = 0.0
        self.current_e = 0.0
        self.offset_e = 0.0
        self.total_e = 0.0
        self.max_e = 0.0
        self.current_e_multi = [0.0]
        self.offset_e_multi = [0.0]
        self.total_e_multi = [0.0]
        self.max_e_multi = [0.0]
        self.cutting = False
        self.cutting_as_extrusion = False
        self.filament_length_multi = []
        self.filament_length = 0.0
        self.home_x = 0.0
        self.home_y = 0.0
        self.home_z = 0.0

        self.duration = 0.0

    def init_values(self):
        self.imperial = False
        self.relative = False
        self.relative_e = False
        self.current_tool = 0
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_z = 0.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.offset_z = 0.0
        self.current_e = 0.0
        self.offset_e = 0.0
        self.total_e = 0.0
        self.max_e = 0.0
        self.current_e_multi = [0.0]
        self.offset_e_multi = [0.0]
        self.total_e_multi = [0.0]
        self.max_e_multi = [0.0]
        self.cutting = False
        self.cutting_as_extrusion = False
        self.filament_length_multi = []
        self.filament_length = 0.0
        self.home_x = 0.0
        self.home_y = 0.0
        self.home_z = 0.0

        self.duration = 0.0


    def calc_time(self, build_layers = False,
                    layer_callback = None):
        """Checks for imperial/relativeness settings and tool changes"""

        self.virtual_sd = self.printer.lookup_object('virtual_sdcard', None)
        # logging.info(f"self.virtual_sd: {self.virtual_sd}")

        virtual_sd_status = self.virtual_sd.get_status()
        self.filepath = virtual_sd_status['file_path']
        # self.filepath = "/home/mks/printer_data/gcodes/star_slic3r_modified.gcode"
        self.data = open(self.filepath, 'r')

        # open file -> get lines -> start calculation=
        if self.data:
            line_class = self.line_class
            self.lines = [line_class(l2) for l2 in
                          (l.strip() for l in self.data)
                          if l2]
            
        lines = self.lines
        imperial = self.imperial
        relative = self.relative
        relative_e = self.relative_e
        current_tool = self.current_tool
        current_x = self.current_x
        current_y = self.current_y
        current_z = self.current_z
        offset_x = self.offset_x
        offset_y = self.offset_y
        offset_z = self.offset_z

        # Extrusion computation
        current_e = self.current_e
        offset_e = self.offset_e
        total_e = self.total_e
        max_e = self.max_e
        cutting = self.cutting

        current_e_multi = self.current_e_multi[current_tool]
        offset_e_multi = self.offset_e_multi[current_tool]
        total_e_multi = self.total_e_multi[current_tool]
        max_e_multi = self.max_e_multi[current_tool]

        # Store this one out of the build_layers scope for efficiency
        cur_layer_has_extrusion = False

        # Initialize layers and other global computations
        if build_layers:
            # Bounding box computation
            xmin = float("inf")
            ymin = float("inf")
            zmin = 0
            xmax = float("-inf")
            ymax = float("-inf")
            zmax = float("-inf")
            # Also compute extrusion-only values
            xmin_e = float("inf")
            ymin_e = float("inf")
            xmax_e = float("-inf")
            ymax_e = float("-inf")

            # Duration estimation
            # TODO:
            # get device caps from firmware: max speed, acceleration/axis
            # (including extruder)
            # calculate the maximum move duration accounting for above ;)
            lastx = lasty = lastz = None
            laste = lastf = 0
            lastdx = 0
            lastdy = 0
            x = y = e = f = 0.0
            currenttravel = 0.0
            moveduration = 0.0
            totalduration = 0.0
            acceleration = 100.0  # mm/s^2
            layerbeginduration = 0.0

            # Initialize layers
            all_layers = self.all_layers = []
            all_zs = self.all_zs = set()
            layer_idxs = self.layer_idxs = []
            line_idxs = self.line_idxs = []


            last_layer_z = None
            prev_z = None
            cur_z = None
            cur_lines = []

            def append_lines(lines, isEnd):
                if not build_layers:
                    return
                nonlocal layerbeginduration, last_layer_z
                if cur_layer_has_extrusion and prev_z != last_layer_z \
                        or not all_layers:
                    layer = Layer([], prev_z)
                    last_layer_z = prev_z
                    finished_layer = len(all_layers)-1 if all_layers else None
                    all_layers.append(layer)
                    all_zs.add(prev_z)
                else:
                    layer = all_layers[-1]
                    finished_layer = None
                layer_id = len(all_layers)-1
                layer_line = len(layer)
                for i, ln in enumerate(lines):
                    layer.append(ln)
                    layer_idxs.append(layer_id)
                    line_idxs.append(layer_line+i)
                layer.duration += totalduration - layerbeginduration
                layerbeginduration = totalduration
                if layer_callback:
                    # we finish a layer when inserting the next
                    if finished_layer is not None:
                        layer_callback(self, finished_layer)
                    # notify about end layer, there will not be next
                    if isEnd:
                        layer_callback(self, layer_id)

        if self.line_class != Line:
            get_line = lambda l: Line(l.raw)
        else:
            get_line = lambda l: l
        for true_line in lines:
            # # Parse line
            # Use a heavy copy of the light line to preprocess
            line = get_line(true_line)
            split_raw = split(line)
            if line.command:
                # Update properties
                if line.is_move:
                    line.relative = relative
                    line.relative_e = relative_e
                    line.current_tool = current_tool
                elif line.command == "G20":
                    imperial = True
                elif line.command == "G21":
                    imperial = False
                elif line.command == "G90":
                    relative = False
                    relative_e = False
                elif line.command == "G91":
                    relative = True
                    relative_e = True
                elif line.command == "M82":
                    relative_e = False
                elif line.command == "M83":
                    relative_e = True
                elif line.command[0] == "T":
                    try:
                        current_tool = int(line.command[1:])
                    except:
                        pass #handle T? by treating it as no tool change
                    while current_tool+1 > len(self.current_e_multi):
                        self.current_e_multi+=[0]
                        self.offset_e_multi+=[0]
                        self.total_e_multi+=[0]
                        self.max_e_multi+=[0]
                elif line.command == "M3" or line.command == "M4":
                    cutting = True
                elif line.command == "M5":
                    cutting = False

                current_e_multi = self.current_e_multi[current_tool]
                offset_e_multi = self.offset_e_multi[current_tool]
                total_e_multi = self.total_e_multi[current_tool]
                max_e_multi = self.max_e_multi[current_tool]


                if line.command[0] == "G":
                    parse_coordinates(line, split_raw, imperial)

                # Compute current position
                if line.is_move:
                    x = line.x
                    y = line.y
                    z = line.z

                    if line.f is not None:
                        self.current_f = line.f

                    if line.relative:
                        x = current_x + (x or 0)
                        y = current_y + (y or 0)
                        z = current_z + (z or 0)
                    else:
                        if x is not None: x = x + offset_x
                        if y is not None: y = y + offset_y
                        if z is not None: z = z + offset_z

                    if x is not None: current_x = x
                    if y is not None: current_y = y
                    if z is not None: current_z = z

                elif line.command == "G28":
                    home_all = not any([line.x, line.y, line.z])
                    if home_all or line.x is not None:
                        offset_x = 0
                        current_x = self.home_x
                    if home_all or line.y is not None:
                        offset_y = 0
                        current_y = self.home_y
                    if home_all or line.z is not None:
                        offset_z = 0
                        current_z = self.home_z

                elif line.command == "G92":
                    if line.x is not None: offset_x = current_x - line.x
                    if line.y is not None: offset_y = current_y - line.y
                    if line.z is not None: offset_z = current_z - line.z

                line.current_x = current_x
                line.current_y = current_y
                line.current_z = current_z

                # # Process extrusion
                if line.e is not None:
                    if line.is_move:
                        if line.relative_e:
                            line.extruding = line.e > 0
                            total_e += line.e
                            current_e += line.e
                            total_e_multi += line.e
                            current_e_multi += line.e
                        else:
                            new_e = line.e + offset_e
                            line.extruding = new_e > current_e
                            total_e += new_e - current_e
                            current_e = new_e
                            new_e_multi = line.e + offset_e_multi
                            total_e_multi += new_e_multi - current_e_multi
                            current_e_multi = new_e_multi

                        max_e = max(max_e, total_e)
                        max_e_multi=max(max_e_multi, total_e_multi)
                        cur_layer_has_extrusion |= line.extruding and (line.x is not None or line.y is not None)
                    elif line.command == "G92":
                        offset_e = current_e - line.e
                        offset_e_multi = current_e_multi - line.e
                if cutting and self.cutting_as_extrusion:
                    line.extruding = True

                self.current_e_multi[current_tool]=current_e_multi
                self.offset_e_multi[current_tool]=offset_e_multi
                self.max_e_multi[current_tool]=max_e_multi
                self.total_e_multi[current_tool]=total_e_multi

                # # Create layers and perform global computations
                if build_layers:
                    # Update bounding box
                    if line.is_move:
                        if line.extruding:
                            if line.current_x is not None:
                                # G0 X10 ; G1 X20 E5 results in 10..20 even as G0 is not extruding
                                xmin_e = min(xmin_e, line.current_x, xmin_e if lastx is None else lastx)
                                xmax_e = max(xmax_e, line.current_x, xmax_e if lastx is None else lastx)
                            if line.current_y is not None:
                                ymin_e = min(ymin_e, line.current_y, ymin_e if lasty is None else lasty)
                                ymax_e = max(ymax_e, line.current_y, ymax_e if lasty is None else lasty)
                        if max_e <= 0:
                            if line.current_x is not None:
                                xmin = min(xmin, line.current_x)
                                xmax = max(xmax, line.current_x)
                            if line.current_y is not None:
                                ymin = min(ymin, line.current_y)
                                ymax = max(ymax, line.current_y)

                    # Compute duration
                    if line.command == "G0" or line.command == "G1":
                        x = line.x if line.x is not None else (lastx or 0)
                        y = line.y if line.y is not None else (lasty or 0)
                        z = line.z if line.z is not None else (lastz or 0)
                        e = line.e if line.e is not None else laste
                        # mm/s vs mm/m => divide by 60
                        f = line.f / 60.0 if line.f is not None else lastf

                        # given last feedrate and current feedrate calculate the
                        # distance needed to achieve current feedrate.
                        # if travel is longer than req'd distance, then subtract
                        # distance to achieve full speed, and add the time it took
                        # to get there.
                        # then calculate the time taken to complete the remaining
                        # distance

                        # FIXME: this code has been proven to be super wrong when 2
                        # subsquent moves are in opposite directions, as requested
                        # speed is constant but printer has to fully decellerate
                        # and reaccelerate
                        # The following code tries to fix it by forcing a full
                        # reacceleration if this move is in the opposite direction
                        # of the previous one
                        dx = x - (lastx or 0)
                        dy = y - (lasty or 0)
                        if dx * lastdx + dy * lastdy <= 0:
                            lastf = 0

                        currenttravel = math.hypot(dx, dy)
                        if currenttravel == 0:
                            if line.z is not None:
                                currenttravel = abs(line.z) if line.relative else abs(line.z - (lastz or 0))
                            elif line.e is not None:
                                currenttravel = abs(line.e) if line.relative_e else abs(line.e - laste)
                        # Feedrate hasn't changed, no acceleration/decceleration planned
                        if f == lastf:
                            moveduration = currenttravel / f if f != 0 else 0.
                        else:
                            # FIXME: review this better
                            # this looks wrong : there's little chance that the feedrate we'll decelerate to is the previous feedrate
                            # shouldn't we instead look at three consecutive moves ?
                            distance = 2 * abs(((lastf + f) * (f - lastf) * 0.5) / acceleration)  # multiply by 2 because we have to accelerate and decelerate
                            if distance <= currenttravel and lastf + f != 0 and f != 0:
                                moveduration = 2 * distance / (lastf + f)  # This is distance / mean(lastf, f)
                                moveduration += (currenttravel - distance) / f
                            else:
                                moveduration = 2 * currenttravel / (lastf + f)  # This is currenttravel / mean(lastf, f)
                                # FIXME: probably a little bit optimistic, but probably a much better estimate than the previous one:
                                # moveduration = math.sqrt(2 * distance / acceleration) # probably buggy : not taking actual travel into account

                        lastdx = dx
                        lastdy = dy

                        totalduration += moveduration

                        lastx = x
                        lasty = y
                        lastz = z
                        laste = e
                        lastf = f
                    elif line.command == "G4":
                        moveduration = P(line)
                        if moveduration:
                            moveduration /= 1000.0
                            totalduration += moveduration

                    # FIXME : looks like this needs to be tested with "lift Z on move"
                    if line.z is not None:
                        if line.command == "G92":
                            cur_z = line.z
                        elif line.is_move:
                            if line.relative and cur_z is not None:
                                cur_z += line.z
                            else:
                                cur_z = line.z

                    if cur_z != prev_z and cur_layer_has_extrusion:
                        append_lines(cur_lines, False)
                        cur_lines = []
                        cur_layer_has_extrusion = False

            if build_layers:
                cur_lines.append(true_line)
                prev_z = cur_z
            # ## Loop done

        # Store current status
        self.imperial = imperial
        self.relative = relative
        self.relative_e = relative_e
        self.current_tool = current_tool
        self.current_x = current_x
        self.current_y = current_y
        self.current_z = current_z
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.offset_z = offset_z
        self.current_e = current_e
        self.offset_e = offset_e
        self.max_e = max_e
        self.total_e = total_e
        self.current_e_multi[current_tool]=current_e_multi
        self.offset_e_multi[current_tool]=offset_e_multi
        self.max_e_multi[current_tool]=max_e_multi
        self.total_e_multi[current_tool]=total_e_multi
        self.cutting = cutting


        # Finalize layers
        if build_layers:
            if cur_lines:
                append_lines(cur_lines, True)

            self.append_layer_id = len(all_layers)
            self.append_layer = Layer([])
            self.append_layer.duration = 0
            all_layers.append(self.append_layer)
            self.layer_idxs = array('I', layer_idxs)
            self.line_idxs = array('I', line_idxs)

            # Compute bounding box
            all_zs = self.all_zs.union({zmin}).difference({None})
            zmin = min(all_zs)
            zmax = max(all_zs)

            self.filament_length = self.max_e
            while len(self.filament_length_multi)<len(self.max_e_multi):
                    self.filament_length_multi+=[0]
            for i in enumerate(self.max_e_multi):
                self.filament_length_multi[i[0]]=i[1]


            if self.filament_length > 0:
                self.xmin = xmin_e if not math.isinf(xmin_e) else 0
                self.xmax = xmax_e if not math.isinf(xmax_e) else 0
                self.ymin = ymin_e if not math.isinf(ymin_e) else 0
                self.ymax = ymax_e if not math.isinf(ymax_e) else 0
            else:
                self.xmin = xmin if not math.isinf(xmin) else 0
                self.xmax = xmax if not math.isinf(xmax) else 0
                self.ymin = ymin if not math.isinf(ymin) else 0
                self.ymax = ymax if not math.isinf(ymax) else 0
            self.zmin = zmin if not math.isinf(zmin) else 0
            self.zmax = zmax if not math.isinf(zmax) else 0
            self.width = self.xmax - self.xmin
            self.depth = self.ymax - self.ymin
            self.height = self.zmax - self.zmin

            # Finalize duration
            totaltime = datetime.timedelta(seconds = int(totalduration))
            self.duration = totaltime
            return int(totalduration)
    
def load_config(config):
    return CalcPrintTime(config)
