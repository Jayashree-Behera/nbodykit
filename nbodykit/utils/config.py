import inspect
import functools
from collections import namedtuple, OrderedDict
import yaml
from argparse import Namespace

class ConfigurationError(Exception): 
    """
    General exception for when configuration parsing fails
    """  
    pass
    
class EmptyConfigurationError(ConfigurationError):
    """
    Specific parsing error when the YAML loader does not
    find any valid keys
    """
    pass
    
class PluginParsingError(ConfigurationError):   
    """
    Specific parsing error when the plugin fails to load from 
    the configuration file
    """
    pass
 
def ordered_load(stream, Loader=yaml.SafeLoader, object_pairs_hook=OrderedDict):
    """
    A wrapper of :func:`yaml.load` that will load the YAML file into a
    :class:`~collections.OrderedDict` rather than a normal :class:`dict` to 
    preserve the ordering used by the user
    
    see: http://stackoverflow.com/questions/5121931/
    """
    class OrderedLoader(Loader):
        pass
    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping)
    return yaml.load(stream, OrderedLoader)


def case_insensitive_name_match(schema_name, config):
    """
    Do case-insensitive name matching between the ConstructorSchema
    and the parsed configuration file
    
    Parameters
    ----------
    schema_name : str
        the name of the parameter, as given in the ConstructorSchema
    config : dict
        the parsed YAML dictionary from the configuration file
    
    Returns
    -------
    config_name : {str, None}
        return the key of `config` that matches `schema_name`; 
        otherwise, return `None`
    """
    # names from the parsed config file
    config_names = list(config.keys())
    lowered_config_names = [k.lower() for k in config_names]
    
    # lowered schema name
    lowered_schema_name = schema_name.lower()
    
    # return the name of the parameter in the configuration file
    if lowered_schema_name in lowered_config_names:
        index = lowered_config_names.index(lowered_schema_name)
        return config_names[index]
        
    return None
    

def fill_namespace(ns, arg, config, missing):
    """
    Recursively fill the input namespace from a dictionary parsed
    from configuration file using YAML
    
    Notes
    -----
    *   Fields that have subfields will be returned as sub-namespaces, such that
        the subfields can be accessed from the parent field with the same
        ``parent.subfield`` syntax
    *   Comparison of names between and configuration file and schema are 
        done in a case-insensitive manner
    *   Before adding to the namespace the values will be case according
        to the `cast` function specified via `arg`
    
    Parameters
    ----------
    ns : argparse.Namespace
        the namespace to fill with the configuration 
    arg : Argument
        the Argument instance that we are adding to the namespace; this
        holds the details about casting, sub-fields, etc
    config : OrderedDict
        an ordered dictionary of parsed YAML holding the input 
        configuration parameters
    missing : list
        a list to add arguments that are missing, i.e., required and not
        present in the input configuration files
    """
    # the name of the parameter (as taken from the schema)
    schema_name = arg.name.split('.')[-1]

    # no subfields
    if not len(arg.subfields):

        # check if the schema argument is present in configuration file

        if config is not None:

            # the name of the parameter match in the configuration file
            # or None, if no match
            config_match = case_insensitive_name_match(schema_name, config)

            if config_match is not None:
                value = config.pop(config_match)
                try:
                    setattr(ns, schema_name, ConstructorSchema.cast(arg, value))
                except Exception as e:
                    import traceback
                    raise ConfigurationError("unable to cast '%s' value: %s" %(arg.name, traceback.format_exc()))
            else:
                if arg.required:
                    missing.append(arg.name)
    else:
        subns = Namespace()
        subconfig = config.pop(schema_name, None)

        for k in arg.subfields:
            fill_namespace(subns, arg[k], subconfig, missing)

        if len(vars(subns)):
            try:
                setattr(ns, schema_name, ConstructorSchema.cast(arg, subns))
            except Exception as e:
                raise ConfigurationError("unable to cast '%s' value: %s" %(arg.name, str(e)))


def ReadConfigFile(config_stream, schema):
    """
    Read parameters from a file using YAML syntax
    
    The function uses the specified `schema` to:
        * infer default values
        * check if parameter values are consistent with `choices`
        * infer the `type` of each parameter
        * check if any required parameters are missing
    
    Parameters
    ----------
    stream : open file object, str
        an open file object or the string returned by calling `read`
    schema : ConstructorSchema
        the schema which tells the parser which holds the relevant 
        information about the necessary parameters
    
    Returns
    -------
    ns : argparse.Namespace
        the namespace holding the parsed configuration file
    unknown : argparse.Namespace
        a namespace holding any parsed parameters not present
        in the scema
    """
    from nbodykit.cosmology import Cosmology
    from nbodykit.extensionpoints import set_nbkit_cosmo
    from nbodykit.pluginmanager import load

    # make a new namespace
    ns, unknown = Namespace(), Namespace()

    # read the yaml config file
    try:
        config = ordered_load(config_stream)
        
        # if the YAML loader returns a string or None
        # then it failed to find any valid keys
        if isinstance(config, (str, type(None))):
            raise EmptyConfigurationError("no valid keys found")
    
    except EmptyConfigurationError:
        raise # just re-raise this type of error
    except Exception as e:
        raise ConfigurationError("error parsing YAML file: %s" %str(e))
    
    # first load any plugins
    plugins = []
    if 'X' in config:
        plugins = config['X']
        if isinstance(plugins, str):
            plugins = [plugins]
        for plugin in plugins: load(plugin)
        config.pop('X')
    
    # now load cosmology
    cosmo = None
    if 'cosmo' in config:
       cosmo = Cosmology(**config.pop('cosmo'))
       set_nbkit_cosmo(cosmo)
                
    # fill the namespace, recursively 
    missing = []
    extra = config.copy()
    for name in schema:
        fill_namespace(ns, schema[name], extra, missing)
    
    # store any unknown values
    for k in extra:
        setattr(unknown, k, extra[k])
    
    # crash if we don't have all required args
    if len(missing):
        raise ValueError("missing required arguments: %s" %str(missing))
    return ns, unknown

ArgumentBase = namedtuple('Argument', ['name', 'required', 'type', 'default', 'choices', 'nargs', 'help', 'subfields'])
class Argument(ArgumentBase):
    """
    Class to represent an argument in the `ConstructorSchema`
    """
    def __new__(cls, name, required, type=None, default=None, choices=None, nargs=None, help="", subfields=None):
        if subfields is None: subfields = OrderedDict()
        return super(Argument, cls).__new__(cls, name, required, type, default, choices, nargs, help, subfields)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self.subfields[key]
        return ArgumentBase.__getitem__(self, key)

    def _asdict(self):
        # FIXME: the override to getitem seems to be messing up Python 3.
        # is it used at all?
        d = {}
        for f in self._fields:
            d[f] = getattr(self, f)
        return d

class ConstructorSchema(OrderedDict):
    """
    An `OrderedDict` of `Argument` objects, which are `namedtuples`.
    Each `Argument` stores the relevant information of that
    argument, included `type`, `help`, `choices`, etc. 
    
    Each `Argument` also stores a `subfields` attribute, which
    is a new `OrderedDict` of `Arguments`, storing any sub-fields
    
    Notes
    -----
    You can test whether a full argument 'name' is in the schema
    with the `contains` function
    
    >> param_name = 'field.DataSource'
    >> contains = schema.contains(param_name)
            
    Arguments that are subfields can be accessed in a sequential 
    dict-like fashion:
    
    >> subarg = schema['field']['DataSource']
    """  
    Argument = Argument 
              
    def __init__(self, description=""):
        super(ConstructorSchema, self).__init__()
        self.description = description
    
    def __repr__(self):
        
        size = len(self)
        required = 0
        for k in self:
            arg = self[k]
            if arg.required: required += 1
            
        args = (self.__class__.__name__, size, size-required)
        return "<%s: %d parameters (%d optional)>" %args

    @staticmethod
    def cast(arg, value):
        """
        Convenience function to cast values based
        on the `type` stored in `schema`. If `type` is a tuple, each
        type will be attempted in order.

        Parameters
        ----------
        arg : Argument
            the `Argument` which gives the relevant metadata
            to properly cast value
        value : 
            the value we are casting, using the `type`
            attribute of `arg`
        """
        if arg.nargs is not None:
            if not isinstance(value, list): value = [value]
        if isinstance(arg.nargs, int) and len(value) != arg.nargs:
            raise ValueError("'%s' requires exactly %d arguments" %(arg.name, arg.nargs))
        if arg.nargs == '+' and len(value) == 0:
            raise ValueError("'%s' requires at least one argument" %arg.name)

        def cast1(cast):
            if cast is None: return value
            if arg.nargs is not None:
                r = [cast(v) for v in value]
            else:
                r = cast(value)
            return r

        cast = arg.type
        if not isinstance(arg.type, tuple):
            casts = (cast,)
        else:
            casts = arg.type

        for cast in casts[:-1]:
            try:
                return cast1(cast)
            except Exception as e:
                pass

        return cast1(casts[-1])

    def contains(self, key):
        """
        Check if the schema contains the full argument name, using
        `.` to represent subfields
        """
        split = key.split('.')
        prefix = split[:-1]; name = split[-1]
        obj = self
        for k in prefix:
            obj = self[k].subfields
        
        return name in obj 
    
    def add_argument(self, name, type=None, default=None, choices=None, nargs=None, help=None, required=False):
        """
        Add an argument to the schema
        
        Parameters
        ----------
        name : str
            the name of the parameter to add
        type : callable, optional
            a function that will cast the parsed value
        default : optional
            the default value for this parameter
        choices : optional
            the distinct values that the parameter can take
        nargs : int, '*', '+', optional
             the number of arguments that should be consumed for this parameter
        help : str, optional
            the help string
        required : bool, optional
            whether the parameter is required or not    
        """                
        # get the prefix
        split = name.split('.')
        prefix = split[:-1]; suffix = split[-1]
        
        # create default parent Arguments that do not exist
        obj = self
        for i, k in enumerate(prefix):
            if k not in obj:
                obj[k] = Argument('.'.join(prefix[:i+1]), required)
            obj = obj[k].subfields
        
        # add new argument (with empty subfields)
        if not self.contains(name):
            obj[suffix] = Argument(name, required, nargs=nargs, type=type, default=default, 
                                    choices=choices, help=help)
                                    
        # overwrite existing object (copying the subfields)
        else:
            obj[suffix] = obj[suffix]._replace(type=type, default=default, choices=choices, 
                                                help=help, required=required, nargs=nargs)
     
    def _arg_info(self, name, arg, level, subfield=False):
        """
        Internal helper function that returns the info string 
        for one argument, indenting to match a specific level
        
        Format: name: description (default=`default`)
        """  
        indent = " "*4
        space = indent*level
        
        # determine the string representation of the type 
        if arg.choices is not None:
            type_str = "{ %s }" %", ".join(["'%s'" %str(s) for s in arg.choices])
        else:
            if isinstance(arg.type, tuple):
                casts = arg.type
            else:
                casts = (arg.type,)

            type_str = []
            for cast in casts:
                cstr = cast.__name__ if arg.type is not None else ""
                if hasattr(cast, '__self__'):
                    cstr = '.'.join([cast.__self__.__name__, cast.__name__])

                # don't use function names when it's a lambda function
                if 'lambda' in cstr: cstr = ""
                type_str.append(cstr)
            type_str = ', '.join(type_str)

        # optional tag?
        if not subfield and not arg.required:
            if type_str: type_str += ", "
            type_str += 'optional'
            
        # first line is name : type, indented `level` times
        info = "%s%s : %s\n" %(space, name, type_str)
        
        # second line gives the description, indented `level+1` times
        info += "%s%s" %(indent*(level+1), arg.help)
        if arg.default is not None:
            info += " (default: %s)" %arg.default
        return info
                     
    def _parse_info(self, name, arg, level, subfield=False):
        """
        Internal function to recursively parse a argument and any
        subfields, returning the full into string
        """
        indent = " "*4
        info = self._arg_info(name, arg, level, subfield=subfield)
        if not len(arg.subfields):
            return info
        
        info += "\n" + indent*(level) + "    " + "The %d subfields are:" %(len(arg.subfields))
        info += '\n'
        for k in arg.subfields:
            v = arg.subfields[k]
            info += '\n'+self._parse_info(k, v, level+2, subfield=True)
        info += '\n'
             
        return info
            
    def format_help(self):
        """
        Return a string giving the help using the 
        format preferred by the ``numpy`` documentation
        """
        toret = """"""
        if getattr(self, 'description', ""):
            toret += self.description + '\n\n'
            
        optional = []; required = []
        for k in self:
            arg = self[k]
            info = self._parse_info(k, arg, level=0)
            if arg.required:
                required.append(info)
            else:
                optional.append(info)
            
        toret += "Parameters\n----------\n"
        toret += "\n".join(required + optional)
        return toret
        
    __str__ = format_help
        
def attribute(name, **kwargs):
    """
    Declare a class attribute, adding it to the schema attached to 
    the function we are decorating
    """
    def _argument(func):
        if not hasattr(func, 'schema'):
            func.schema = ConstructorSchema()
        func.schema.add_argument(name, **kwargs)
        return func
    return _argument
    
def autoassign(init, attach_comm=True, attach_cosmo=False):
    """
    Verify the schema attached to the input `init` function,
    automatically set the input arguments, and then finally
    call `init`
    
    Parameters
    ----------
    init : callable
        the function we are decorating
    allowed : list, optional
        list of names of additional attributes that are allowed to be 
        auto-assigned if passed to the function -- useful for when
        we are automatically setting the cosmology
    attach_comm : bool, optional
        if `True`, set the `comm` attribute to the return value
        of `get_nbkit_comm`; default: True
    """
    # inspect the function
    attrs, varargs, varkw, defaults = inspect.getargspec(init)
    if defaults is None: defaults = []
    
    allowed = []
    if attach_cosmo:
        if 'cosmo' not in init.schema:
            h = 'the `Cosmology` class relevant for the DataSource'
            init.schema.add_argument("cosmo", default=None, help=h)
        allowed.append('cosmo')
    if attach_comm:
        if 'comm' not in init.schema:
            h = 'the global MPI communicator'
            init.schema.add_argument("comm", default=None, help=h)
        allowed.append('comm')
         
    # verify the schema
    update_schema(init, attrs, defaults, allowed=allowed)
         
    @functools.wraps(init)
    def wrapper(self, *args, **kwargs):
        
        # attach the global communicator
        if attach_comm:
            from nbodykit.extensionpoints import get_nbkit_comm
            self.comm = get_nbkit_comm()

        # attach the global cosmology
        if attach_cosmo:
            from nbodykit.extensionpoints import get_nbkit_cosmo
            self.cosmo = get_nbkit_cosmo()
        
        # handle extra allowed keywords (that aren't in signature)
        for k in allowed:
            if k in kwargs:
                setattr(self, k, kwargs.pop(k))
        
        # handle default values
        for attr, val in zip(reversed(attrs), reversed(defaults)):
            setattr(self, attr, val)
        
        # handle positional arguments
        positional_attrs = attrs[1:]
        posargs = {}            
        for attr, val in zip(positional_attrs, args):
            check_choices(init.schema, attr, val)
            posargs[attr] = val
            setattr(self, attr, val)
    
        # handle varargs
        if varargs:
            remaining_args = args[len(positional_attrs):]
            setattr(self, varargs, remaining_args)            
        
        # handle varkw
        if kwargs:
            for attr,val in kwargs.items():
                check_choices(init.schema, attr, val)
                setattr(self, attr, val)
        
        # call the __init__ to confirm proper initialization
        try:
            return init(self, *args, **kwargs)
        except Exception as e:
            
            # get the error message
            errmsg = get_init_errmsg(init.schema, posargs, kwargs)
            
            # format the total message
            args = (self.__class__.__name__,)
            msg = '\n' + '-'*75 + '\n'
            msg += "error initializing __init__ for '%s':\n" %self.__class__.__name__
            msg += "\t%-25s: '%s'\n" %("original error message", str(e))
            if len(errmsg): msg += "%s\n" %errmsg
            msg += '-'*75 + '\n'
            e.args = (msg, )
            raise
            
    return wrapper
    
def get_init_errmsg(schema, posargs, kwargs):
    """
    Return a reasonable error message, accounting for:
    
        * missing arguments
        * extra arguments
        * duplicated positional + keyword arguments
    """
    errmsg = ""
    
    # check duplicated
    duplicated = list(set(posargs.keys()) & set(kwargs.keys()))
    if len(duplicated):
        s = "duplicated arguments"
        errmsg += "\t%-25s: %s\n" %(s, str(duplicated))
        
    # check for missing arguments
    required = [s for s in schema if schema[s].required]
    missing = []
    for r in required:
        if r not in posargs and r not in kwargs:
            missing.append(r)
    if len(missing):
        s = "missing arguments"
        errmsg += "\t%-25s: %s\n" %(s, str(missing))
    
    # check for extra arguments
    keys = list(set(posargs.keys()) | set(kwargs.keys()))
    extra = []
    for k in keys:
        if k not in schema:
            extra.append(k)
    if len(extra):
        s = "extra arguments"
        errmsg += "\t%-25s: %s\n" %(s, str(extra))
    
    return errmsg
    

def check_choices(schema, attr, val):
    """
    Verify that the input values are consistent
    with the `choices`, using the schema
    """
    if attr in schema:
        arg = schema[attr]
        if arg.choices is not None:
            if val not in arg.choices:
                raise ValueError("valid choices for '%s' are: '%s'" %(arg.name, str(arg.choices)))

def update_schema(func, attrs, defaults, allowed=[]):
    """
    Update the schema, which is attached to `func`,
    using information gather from the function's signature, 
    namely `attrs` and `defaults`
    
    This will update the `required` and `default` values
    of the schema, using the signature of `func`
    
    It also verifies certain aspects of the schema, mostly as a
    consistency check on the developer
    
    The `allowed` list provides the names of the parameters
    not in the function signature that are still allowed, because
    they will be set before the function call
    """
    args = attrs[1:] # ignore self

    # get the required names and default names
    required = args; default_names = []
    if defaults is not None and len(defaults):
        required = args[:-len(defaults)]  
        default_names = args[-len(defaults):]
    
    # loop over the schema arguments
    extra = []; missing = default_names + required
    for name in func.schema:
        a = func.schema[name]

        # infer required and defaults and update them
        d = a._asdict()
        d['required'] = a.name in required
        if a.name in default_names:
            d['default'] = defaults[default_names.index(a.name)]

        func.schema[name] = func.schema.Argument(**d)

        # check for extra and missing
        if a.name not in args and a.name not in allowed:
            extra.append(a.name)
        elif a.name in missing:
            missing.remove(a.name)

    # crash if we are missing or got extra (sanity check)
    if len(missing):
        raise ValueError("missing arguments in schema : %s " %str(missing))
    if len(extra):
        raise ValueError("extra arguments in schema : %s" %str(extra))

    # reorder the schema list to match the function signature
    schema_keys = [k for k in func.schema.keys() if k in args]
    if schema_keys != args:
        new_schema = ConstructorSchema(description=func.schema.description)
        for a in args:
            if a in func.schema:
                new_schema[a] = func.schema[a]
        for p in allowed: 
            if p in func.schema:
                new_schema[p] = func.schema[p]
        func.schema = new_schema
        
    # update the doc with the schema documentation
    if func.__doc__:
        func.__doc__ += "\n\n" + func.schema.format_help()
    else:
        func.__doc__ = func.schema.format_help()
