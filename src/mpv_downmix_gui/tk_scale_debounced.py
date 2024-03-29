import tkinter as tk
from tkinter import ttk

class tk_scale_debounced(ttk.Frame):

    """
    scale with after_change event

    aka: debounced scale

    example:

    def after_change(key, value):
        print(key, value)
    def get_value(value):
        return 10**(value/10)
    root = tk.Tk()
    s = tk_scale_debounced(
        root, "some label", after_change, key="x",
        get_value=get_value, from_=-10, to=10
    )
    s.pack()
    """

    # current value
    value = None

    # debounce timer for keyboard input
    _change_key_timer = None

    def __init__(
            self,
            parent,
            label,
            after_change, # lambda key, value: None
            on_change=None, # lambda key, value: None
            key=None,
            get_value=lambda x: x,
            set_value=lambda x: x,
            init_value=0.0,
            from_=0,
            to=1,
            format="%.2f",
            **scale_kwargs
        ):

        """
        example scale_kwargs:

        from_=-10, to=10, orient='horizontal'
        """

        super().__init__(parent)

        self._after_change = after_change
        self._on_change = on_change
        self._label = label
        self._key = key or label
        self._get_value = get_value
        self._set_value = set_value
        self._format = format
        self._init_value = init_value
        self._from = from_
        self._to = to
        self._last_value = self._init_value

        self.value = tk.DoubleVar()
        self.value.set(self._set_value(self._init_value))

        #print(f"set init: {self._key}: {self._init_value} -> {self._set_value(self._init_value)} -> {self.value.get()}")

        #self.columnconfigure(0, weight=2)
        #self.columnconfigure(1, weight=1)
        #self.columnconfigure(2, weight=100)

        # label
        self._scale_label = ttk.Label(self, text=self._label)
        self._scale_label.grid(column=0, row=0, sticky='w')

        # value
        self._value_label = ttk.Label(self, text=self._format_value())
        self._value_label.grid(column=1, row=0, sticky='e')

        #  scale
        self._scale = ttk.Scale(
            self,
            command=self._scale_change_live,
            variable=self.value,
            from_=self._set_value(self._from),
            to=self._set_value(self._to),
            **scale_kwargs
        )
        #self._scale.grid(column=2, row=0, columnspan=2, sticky='we')
        self._scale.grid(column=0, row=1, columnspan=2, sticky='we')
        #self._scale.set(self._set_value(self._init_value))

        # mouse
        self._scale.bind("<ButtonRelease-1>", self._scale_change_done)
        # keyboard
        self._scale.bind("<KeyRelease>", self._scale_change_key)

    def get(self):
        return self._get_value(self.value.get())

    def set(self, value):
        self.value.set(self._set_value(value))
        self._value_label.configure(text=self._format_value())

    def _format_value(self):
        return self._format % self._get_value(self.value.get())

    def _scale_change_live(self, event):
        self._value_label.configure(text=self._format_value())
        if self._on_change:
            self._on_change(self._key, self._get_value(self.value.get()))

    def _scale_change_done(self, event=None):
        value = self._get_value(self.value.get())
        if value != self._last_value:
            self._after_change(self._key, value)
            self._last_value = value

    def _scale_change_key(self, event):
        if self._change_key_timer:
            self.after_cancel(self._change_key_timer)
        t = 1000
        self._change_key_timer = self.after(t, self._scale_change_done)
