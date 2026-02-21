class KeyBindings:
    """Default key bindings mapping keysyms to actions."""

    @staticmethod
    def get_defaults():
        return {
            'Up':           'max',
            'Down':         'center',
            'Left':         'left',
            'Right':        'right',
            'space':        'restore',

            'KP_Home':      'top-left',
            'KP_Up':        'top-center',
            'KP_Page_Up':   'top-right',
            'KP_Left':      'left',
            'KP_Begin':     'center',
            'KP_Right':     'right',
            'KP_End':       'bottom-left',
            'KP_Down':      'bottom-center',
            'KP_Page_Down': 'bottom-right',
            'KP_Insert':    'restore',

            'KP_Prior':     'top-right',
            'KP_Next':      'bottom-right',

            'KP_Add':       'bigger',
            'KP_Subtract':  'smaller',

            'BackSpace':    'exit',
        }
