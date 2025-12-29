# Ragnok4Linux 🐧🖱️

![ErgoStrike7 Mouse](https://cdn.4fingerstudios.com/gun.png)

**Ragnok4Linux** is a Linux tray application for configuring the **ErgoStrike7** gaming mouse.

This project was created after receiving the mouse and discovering that it only had official support for Windows. Rather than relying on Windows software, the mouse protocol was reverse-engineered and the functionality was ported to Linux.

## Features

The tray application provides all core features needed for everyday use:

- DPI read and set
- Polling rate control
- Angle Snap toggle
- Motion Sync toggle
- Ripple Control toggle
- LED mode selection (1–5)
- Custom RGB color picker (mode 2)
- LED brightness and speed control
- Battery status and sleep detection
- Button 4 macro support
- String-based keyboard macros
- Lightweight GTK tray interface

## Project Structure

src/
 ├── backend.py    # HID protocol handling and device logic
 └── frontend.py   # GTK tray user interface

## Building

The project can be built into a single Linux binary using **PyInstaller**.

### Requirements

- Python 3.9+
- GTK 3
- PyGObject

Install build dependencies:

pip install pyinstaller

### Build Command

From the project root:

pyinstaller --onefile --name ragnok \
  --hidden-import=gi \
  --hidden-import=gi.repository.Gtk \
  --hidden-import=gi.repository.GLib \
  --hidden-import=gi.repository.Notify \
  --hidden-import=gi.repository.AyatanaAppIndicator3 \
  src/frontend.py

The resulting binary will be located in:

dist/ragnok

## Why This Exists

The ErgoStrike7 hardware itself is solid, but the lack of Linux support was a blocker. This project exists to provide a native Linux solution without requiring Windows.

## Contributing

Contributions and feedback are welcome. If you have ideas, improvements, or fixes, feel free to open an issue or submit a pull request.

## Disclaimer

This is an unofficial Linux implementation. Use at your own risk when writing configuration data to device flash memory.

Enjoy using your mouse on Linux 🐧🖱️
