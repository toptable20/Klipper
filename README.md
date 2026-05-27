# Klipper for TopTable

This is a customized version of the Klipper firmware tailored for TopTable-style 3D printers.

## Overview

This repository contains a modified version of Klipper optimized for TopTable printer configurations. It maintains the core functionality and structure of Klipper while including TopTable-specific settings and enhancements.

The `extruder home` functionality has been integrated from an external repository:
- Source: https://github.com/naikymen/klipper-for-cnc/tree/pipetting

## Key Customizations

- Motion control and axis configuration adapted for TopTable build style
- Custom `extruder home` implementation
- TopTable-specific tuning and configuration options

## Getting Started

1. Clone this repository:
   ```bash
   git clone <repository-url>
   ```

2. For build and installation instructions, refer to the official Klipper documentation:
   - https://www.klipper3d.org/Installation.html

3. Use the example configuration files in the `config/` directory as a starting point for your setup.

## License

This project is based on Klipper and is distributed under the GNU General Public License v3.0 (GPLv3).
See the [COPYING](COPYING) file for the full license text.

## References

- Official Klipper Project: https://www.klipper3d.org/
- Extruder Home Source: https://github.com/naikymen/klipper-for-cnc/tree/pipetting

## Important Notes

- This customization is designed specifically for TopTable-style 3D printers.
- The `extruder home` logic incorporates code and configurations from external sources. Please review the implementation carefully before deployment.
