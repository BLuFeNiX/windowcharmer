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

### Scripting

```
usage: windowcharmer [-h] [-d]
                     [{left,center,right,top-left,bottom-left,top-right,bottom-right,top-center,bottom-center,max,restore,cycle,install,bigger,smaller,test}]
```

For example, to move the currently focused window to the right side of the screen: `windowcharmer right`

## Support

For issues, questions, or contributions, please refer to the [issue tracker](https://github.com/BLuFeNiX/windowcharmer/issues).