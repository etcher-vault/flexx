""" Base class for objects that live in both Python and JS

This basically implements the syncing of signals.
"""

import sys
import json
import weakref
import hashlib

from ..react import react
from ..react.pyscript import create_js_signals_class, HasSignalsJS

from ..pyscript import js, JSCode
from ..pyscript.parser2 import get_class_definition

from .serialize import serializer

if sys.version_info[0] >= 3:
    string_types = str,
else:
    string_types = basestring,


pair_classes = []
def get_pair_classes():
    """ Get a list of all known Pair subclasses.
    """
    return [c for c in react.HasSignalsMeta.CLASSES if issubclass(c, Pair)]


def get_instance_by_id(id):
    """ Get instance of Pair class corresponding to the given id,
    or None if it does not exist.
    """
    return Pair._instances.get(id, None)


import json


class JSSignal(react.SourceSignal):
    """ A signal that represents a proxy to a signal in JavaScript.
    """
    
    def __init__(self, func_or_name, upstream=[], frame=None, ob=None):
        
        def func(v):
            return v
        
        if isinstance(func_or_name, string_types):
            func.__name__ = func_or_name
        else:
            func.__name__ = func_or_name.__name__
        
        self._linked = False
        react.SourceSignal.__init__(self, func, [], ob=ob)
    
    def _subscribe(self, *args):
        react.SourceSignal._subscribe(self, *args)
        if not self._linked:
            self.__self__._link_js_signal(self.name)
    
    def _unsubscribe(self, *args):
        react.SourceSignal._unsubscribe(self, *args)
        if self._linked and not self._downstream:
            self.__self__._link_js_signal(self.name, False)


class PySignal(react.SourceSignal):
    """ A signal in JS that represents a proxy to a signal in Python.
    """
    
    def __init__(self, name):
        
        def func(v):
            return v
        func._name = name
        react.SourceSignal.__init__(self, func, [])


class PyInputSignal(PySignal):
    """ A signal in JS that represents an input signal in Python. On
    the JS side, this can be used as an input too, although there is
    no validation in this case.
    """
    pass


class PairMeta(react.HasSignalsMeta):
    """ Meta class for Pair
    Set up proxy signals in Py/JS.
    """
 
    def __init__(cls, name, bases, dct):
        react.HasSignalsMeta.__init__(cls, name, bases, dct)
        
        OK_MAGICS = '__init__', '__json__', '__from_json__'
        
        # Create proxy signals on cls for each signal on JS
        if 'JS' in cls.__dict__:
            for name, val in cls.JS.__dict__.items():
                if isinstance(val, react.Signal) and not isinstance(val, PySignal):
                    if not hasattr(cls, name):
                        cls.__signals__.append(name)
                        setattr(cls, name, JSSignal(name))
                    elif isinstance(getattr(cls, name), JSSignal):
                        pass  # ok, overloaded signal on JS side
                    else:
                        print('Warning: JS signal %r not proxied, as it would hide a Py attribute.' % name)
        
        # Implicit inheritance for JS "sub"-class
        jsbases = [getattr(b, 'JS') for b in cls.__bases__ if hasattr(b, 'JS')]
        JS = type('JS', tuple(jsbases), {})
        for c in (cls, ): #cls.__bases__ + (cls, ):
            if 'JS' in c.__dict__:
                if '__init__' in c.JS.__dict__:
                    JS.__init__ = c.JS.__init__
                for name, val in c.JS.__dict__.items():
                    if not name.startswith('__'):
                        setattr(JS, name, val)
                    elif name in OK_MAGICS:
                        setattr(JS, name, val)
        cls.JS = JS
        
        # Create proxy signals on cls.JS for each signal on cls
        for name, val in cls.__dict__.items():
            if isinstance(val, react.Signal) and not isinstance(val, JSSignal):
                if not hasattr(cls.JS, name):
                    if isinstance(val, react.InputSignal):
                        setattr(cls.JS, name, PyInputSignal(name))
                    else:
                        setattr(cls.JS, name, PySignal(name))
                elif isinstance(getattr(cls.JS, name), PySignal):
                    pass  # ok, overloaded signal on JS side
                else:
                    print('Warning: Py signal %r not proxied, as it would hide a JS attribute.' % name)
        
        # Set JS and CSS for this class
        cls.JS.CODE = cls._get_js()
        cls.CSS = cls.__dict__.get('CSS', '')
    
    def _get_js(cls):
        """ Get source code for this class.
        """
        cls_name = 'flexx.classes.' + cls.__name__
        base_class = 'flexx.classes.%s.prototype' % cls.mro()[1].__name__
        code = []
        # Add JS version of HasSignals when this is the Pair class
        if cls.mro()[1] is react.HasSignals:
            c = js(serializer.__class__).jscode[4:]  # skip 'var '
            code.append(c.replace('Serializer', 'flexx.Serializer'))
            code.append('flexx.serializer = new flexx.Serializer();')
            c = HasSignalsJS.jscode[4:]  # skip 'var '
            code.append(c.replace('HasSignals', 'flexx.classes.HasSignals'))
        # Add this class
        code.append(create_js_signals_class(cls.JS, cls_name, base_class))
        if cls.mro()[1] is react.HasSignals:
            code.append('flexx.serializer.add_reviver("Flexx-Pair", flexx.classes.Pair.prototype.__from_json__);\n')
        return '\n'.join(code)


class Pair(react.with_metaclass(PairMeta, react.HasSignals)):
    """ Class representing Python-JavaScript object pairs
    
    Each instance of this class has a corresponding object in
    JavaScript, and their signals are synced both ways. Signals defined
    in Python can be connected to from JS, and vice versa.
    
    The JS version of this class is defined by the contained ``JS``
    class. One can define methods, signals, and (to some extend)
    constants on the JS class:
    
        class MyPair(Pair):
        
            def a_python_method(self):
               ...
            
            class JS:
            
                def a_js_method(this):
                    ...
    
    Parameters:
        _proxy: intended for internal use, the proxy object for this class.
        kwargs: initial signal values (see HasSignals).
    
    Notes:
        This class provides the base object for all widget classes in
        ``flexx.ui``. However, one can also create subclasses that have
        nothing to do with user interfaces or DOM elements. You could e.g.
        use it to calculate pi on nodejs.
    
    """
    
    # Keep track of all instances, so we can easily collect al JS/CSS
    _instances = weakref.WeakValueDictionary()
    
    # Count instances to give each instance a unique id
    _counter = 0
    
    # CSS for this class (no css in the base class)
    CSS = ""
    
    def __json__(self):
        return {'__type__': 'Flexx-Pair', 'id': self.id}
    
    def __from_json__(dct):
        return get_instance_by_id(dct['id'])
    
    def __init__(self, _proxy=None, **kwargs):
        
        # # Start without proxy. While it is None, we collect signal links
        # # and signal values, so that we can pass that info to JS init.
        # self._proxy = None
        # self._initial_signal_values = {}
        # self._initial_signal_links = set()
        
        # Set id and register this instance
        Pair._counter += 1
        self._id = self.__class__.__name__ + str(Pair._counter)
        Pair._instances[self._id] = self
        
        # Init proxy
        if _proxy is None:
            from .proxy import manager
            _proxy = manager.get_default_proxy()
        self._proxy = _proxy
        
        # Instantiate JavaScript version of this class
        clsname = 'flexx.classes.' + self.__class__.__name__
        cmd = 'flexx.instances.%s = new %s(%r);' % (self._id, clsname, self._id)
        self._proxy._exec(cmd)
        
        self._init()
        
        # Init signals - signals will be connected updated, causing updates
        # on the JS side.
        react.HasSignals.__init__(self, **kwargs)
    
    def _init(self):
        """ Can be overloaded when creating a custom class.
        """
        pass
    
    @property
    def id(self):
        """ The unique id of this Pair instance """
        return self._id
    
    @property
    def proxy(self):
        """ The proxy object that connects us to the runtime.
        """
        return self._proxy
    
    def _signal_changed(self, signal):
        if not isinstance(signal, JSSignal):
            #txt = json.dumps(signal.value)
            txt = serializer.saves(signal.value)
            cmd = 'flexx.instances.%s._set_signal_from_py(%r, %r);' % (self._id, signal.name, txt)
            self._proxy._exec(cmd)
    
    def _link_js_signal(self, name, link=True):
        """ Make a link between a JS signal and its proxy in Python.
        This is done when a proxy signal is used as input for a signal
        in Python.
        """
        # if self._proxy is None:
        #     self._initial_signal_links.discart(name)
        #     if link:
        #         self._initial_signal_links.add(name)
        # else:
        link = 'true' if link else 'false'
        cmd = 'flexx.instances.%s._link_js_signal(%r, %s);' % (self._id, name, link)
        self._proxy._exec(cmd)
    
    def call_js(self, call):
        cmd = 'flexx.instances.%s.%s;' % (self._id, call)
        self._proxy._exec(cmd)
    
    
    class JS:
        
        def __json__(self):
            return {'__type__': 'Flexx-Pair', 'id': self.id}
        
        def __from_json__(dct):
            return flexx.instances[dct.id]
        
        def __init__(self, id):
            # Set id alias. In most browsers this shows up as the first element
            # of the object, which makes it easy to identify objects while
            # debugging. This attribute should *not* be used.
            self.__id = self._id = self.id = id
            
            self._linked_signals = {}  # use a list as a set
            
            # Call _init now. This gives subclasses a chance to init at a time
            # when the id is set, but *before* the signals are connected.
            self._init()
            
            # Call HasSignals __init__, signals will be created and connected.
            # Act signals relying on JS signals will fire. 
            # Act signals relying on Py signals will fire later.
            super().__init__()
        
        def _init(self):
            pass
        
        def _set_signal_from_py(self, name, text):
            self._signal_emit_lock = True  # do not send back to py
            #value = JSON.parse(text)
            value = flexx.serializer.loads(text)
            self[name]._set(value)
        
        def _signal_changed(self, signal):
            if flexx.ws is None:  # we could be exported or in an nbviewer
                return
            if self._signal_emit_lock:
                self._signal_emit_lock = False
                return
            # todo: what signals do we sync? all but private signals? or only linked?
            # signals like `children` should always sync, signals like a 100Hz timer not, mouse_pos maybe neither unless linked against
            #if signal.signal_type == 'PyInputSignal' or self._linked_signals[signal._name]:
            if signal.signal_type != 'PySignal' and not signal._name.startswith('_'):
                #txt = JSON.stringify(signal.value)
                txt = flexx.serializer.saves(signal.value)
                flexx.ws.send('SIGNAL ' + self.id + ' ' + signal._name + ' ' + txt)
        
        def _link_js_signal(self, name, link):
            if link:
                self._linked_signals[name] = True
                signal = self[name]
                if signal._timestamp > 1:
                    self._signal_changed(self[name])
            elif self._linked_signals[name]:
                del self._linked_signals[name]
        
        @react.source
        def stub_mouse_pos(pos=(0, 0)):
            return tuple(float(p[0]), float(p[1]))
        
        ## JS event system
        
        # def _proxy_event(self, element, name):
        #     """ Easily get JS events from DOM elements in our event system.
        #     """
        #     that = this
        #     element.addEventListener(name, lambda ev: that.emit_event(name, {'cause': ev}), False)
        # 
        # def _connect_js_event(self, element, event_name, method_name):
        #     """ Connect methods of this object to JS events.
        #     """
        #     that = this
        #     element.addEventListener(event_name, lambda ev: that[method_name](ev), False)


# Make pair objects de-serializable
serializer.add_reviver('Flexx-Pair', Pair.__from_json__)
