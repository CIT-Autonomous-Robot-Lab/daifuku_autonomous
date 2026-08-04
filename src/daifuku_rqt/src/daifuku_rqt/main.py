"""Standalone entry point so the panel can be opened without the rqt shell.

`rqt --standalone daifuku_rqt` does the same thing; this exists so
`ros2 run daifuku_rqt daifuku_rqt` also works.
"""

import sys

from rqt_gui.main import Main


def main():
    return Main().main(
        sys.argv, standalone="daifuku_rqt.control_panel.ControlPanel"
    )


if __name__ == "__main__":
    sys.exit(main())
