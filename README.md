# WindowCharmer

WindowCharmer is a three-column window tiler for ultra-wide monitors, designed to be compatible with the Cinnamon desktop environment. It can likely work on any X11 environment, but this has not been tested and may need tweaking.

## Installation

```sh
git clone https://github.com/BLuFeNiX/windowcharmer
cd windowcharmer
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

## Usage

### Daemon Mode

```sh
cd windowcharmer
source .venv/bin/activate
windowcharmer -d
```

You will likely want to run this automatically on login, which is an exercise left for the user.

#### Default Keybindings

The activation key is Super_L (the left "Windows" key), and the bindings can currently only be changed from source code:
```
    key_combinations = {
        ('Super_L', 'Up'):          lambda: do_action("max"),           # Up
        ('Super_L', 'Down'):        lambda: do_action("center"),        # Down
        ('Super_L', 'Left'):        lambda: do_action("left"),          # Left
        ('Super_L', 'Right'):       lambda: do_action("right"),         # Right
        ('Super_L', 'Space'):       lambda: do_action("restore"),       # Spacebar

        ('Super_L', 'KP_Home'):     lambda: do_action("top-left"),      # Numpad 7
        ('Super_L', 'KP_Up'):       lambda: do_action("top-center"),    # Numpad 8
        ('Super_L', 'KP_Page_Up'):  lambda: do_action("top-right"),     # Numpad 9
        ('Super_L', 'KP_Left'):     lambda: do_action("left"),          # Numpad 4
        ('Super_L', 'KP_Begin'):    lambda: do_action("center"),        # Numpad 5
        ('Super_L', 'KP_Right'):    lambda: do_action("right"),         # Numpad 6
        ('Super_L', 'KP_End'):      lambda: do_action("bottom-left"),   # Numpad 1
        ('Super_L', 'KP_Down'):     lambda: do_action("bottom-center"), # Numpad 2
        ('Super_L', 'KP_Page_Down'):lambda: do_action("bottom-right"),  # Numpad 3
        ('Super_L', 'KP_Insert'):   lambda: do_action("restore"),       # Numpad 0

        ('Super_L', 'KP_Prior'):    lambda: do_action("top-right"),     # Numpad 9 (alternate keyboard layout)
        ('Super_L', 'KP_Next'):     lambda: do_action("bottom-right"),  # Numpad 3 (alternate keyboard layout)

        ('Super_L', 'KP_Add'):      lambda: do_action("bigger"),        # Numpad +
        ('Super_L', 'KP_Subtract'): lambda: do_action("smaller"),       # Numpad -
    }
```

### Scripting

```
usage: windowcharmer [-h] [-d]
                     [{left,center,right,top-left,bottom-left,top-right,bottom-right,top-center,bottom-center,max,restore,cycle,install,bigger,smaller,test}]
```

For example, to move the currently focused window to the right side of the screen: `windowcharmer right`

## Support

For issues, questions, or contributions, please refer to the [issue tracker](https://github.com/BLuFeNiX/windowcharmer/issues).