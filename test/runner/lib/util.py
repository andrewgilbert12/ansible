"""Miscellaneous utility functions and classes."""

from __future__ import absolute_import, print_function

import contextlib
import errno
import fcntl
import inspect
import os
import pkgutil
import random
import re
import shutil
import socket
import stat
import string
import subprocess
import sys
import time

from struct import unpack, pack
from termios import TIOCGWINSZ

try:
    from abc import ABC
except ImportError:
    from abc import ABCMeta
    ABC = ABCMeta('ABC', (), {})

try:
    # noinspection PyCompatibility
    from ConfigParser import SafeConfigParser as ConfigParser
except ImportError:
    # noinspection PyCompatibility
    from configparser import ConfigParser

try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote

import lib.types as t

try:
    C = t.TypeVar('C')
except AttributeError:
    C = None


DOCKER_COMPLETION = {}  # type: dict[str, dict[str, str]]
REMOTE_COMPLETION = {}  # type: dict[str, dict[str, str]]
PYTHON_PATHS = {}  # type: dict[str, str]

try:
    MAXFD = subprocess.MAXFD
except AttributeError:
    MAXFD = -1

COVERAGE_CONFIG_PATH = '.coveragerc'
COVERAGE_OUTPUT_PATH = 'coverage'

INSTALL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Modes are set to allow all users the same level of access.
# This permits files to be used in tests that change users.
# The only exception is write access to directories for the user creating them.
# This avoids having to modify the directory permissions a second time.

MODE_READ = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH

MODE_FILE = MODE_READ
MODE_FILE_EXECUTE = MODE_FILE | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
MODE_FILE_WRITE = MODE_FILE | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH

MODE_DIRECTORY = MODE_READ | stat.S_IWUSR | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
MODE_DIRECTORY_WRITE = MODE_DIRECTORY | stat.S_IWGRP | stat.S_IWOTH


def get_docker_completion():
    """
    :rtype: dict[str, dict[str, str]]
    """
    return get_parameterized_completion(DOCKER_COMPLETION, 'docker')


def get_remote_completion():
    """
    :rtype: dict[str, dict[str, str]]
    """
    return get_parameterized_completion(REMOTE_COMPLETION, 'remote')


def get_parameterized_completion(cache, name):
    """
    :type cache: dict[str, dict[str, str]]
    :type name: str
    :rtype: dict[str, dict[str, str]]
    """
    if not cache:
        images = read_lines_without_comments(os.path.join(INSTALL_ROOT, 'test/runner/completion/%s.txt' % name), remove_blank_lines=True)

        cache.update(dict(kvp for kvp in [parse_parameterized_completion(i) for i in images] if kvp))

    return cache


def parse_parameterized_completion(value):
    """
    :type value: str
    :rtype: tuple[str, dict[str, str]]
    """
    values = value.split()

    if not values:
        return None

    name = values[0]
    data = dict((kvp[0], kvp[1] if len(kvp) > 1 else '') for kvp in [item.split('=', 1) for item in values[1:]])

    return name, data


def is_shippable():
    """
    :rtype: bool
    """
    return os.environ.get('SHIPPABLE') == 'true'


def remove_file(path):
    """
    :type path: str
    """
    if os.path.isfile(path):
        os.remove(path)


def read_lines_without_comments(path, remove_blank_lines=False, optional=False):  # type: (str, bool, bool) -> t.List[str]
    """
    Returns lines from the specified text file with comments removed.
    Comments are any content from a hash symbol to the end of a line.
    Any spaces immediately before a comment are also removed.
    """
    if optional and not os.path.exists(path):
        return []

    with open(path, 'r') as path_fd:
        lines = path_fd.read().splitlines()

    lines = [re.sub(r' *#.*$', '', line) for line in lines]

    if remove_blank_lines:
        lines = [line for line in lines if line]

    return lines


def find_executable(executable, cwd=None, path=None, required=True):
    """
    :type executable: str
    :type cwd: str
    :type path: str
    :type required: bool | str
    :rtype: str | None
    """
    match = None
    real_cwd = os.getcwd()

    if not cwd:
        cwd = real_cwd

    if os.path.dirname(executable):
        target = os.path.join(cwd, executable)
        if os.path.exists(target) and os.access(target, os.F_OK | os.X_OK):
            match = executable
    else:
        if path is None:
            path = os.environ.get('PATH', os.path.defpath)

        if path:
            path_dirs = path.split(os.path.pathsep)
            seen_dirs = set()

            for path_dir in path_dirs:
                if path_dir in seen_dirs:
                    continue

                seen_dirs.add(path_dir)

                if os.path.abspath(path_dir) == real_cwd:
                    path_dir = cwd

                candidate = os.path.join(path_dir, executable)

                if os.path.exists(candidate) and os.access(candidate, os.F_OK | os.X_OK):
                    match = candidate
                    break

    if not match and required:
        message = 'Required program "%s" not found.' % executable

        if required != 'warning':
            raise ApplicationError(message)

        display.warning(message)

    return match


def find_python(version, path=None):
    """
    :type version: str
    :type path: str | None
    :rtype: str
    """
    version_info = tuple(int(n) for n in version.split('.'))

    if not path and version_info == sys.version_info[:len(version_info)]:
        python_bin = sys.executable
    else:
        python_bin = find_executable('python%s' % version, path=path)

    return python_bin


def generate_pip_command(python):
    """
    :type python: str
    :rtype: list[str]
    """
    return [python, '-m', 'pip.__main__']


def raw_command(cmd, capture=False, env=None, data=None, cwd=None, explain=False, stdin=None, stdout=None,
                cmd_verbosity=1, str_errors='strict'):
    """
    :type cmd: collections.Iterable[str]
    :type capture: bool
    :type env: dict[str, str] | None
    :type data: str | None
    :type cwd: str | None
    :type explain: bool
    :type stdin: file | None
    :type stdout: file | None
    :type cmd_verbosity: int
    :type str_errors: str
    :rtype: str | None, str | None
    """
    if not cwd:
        cwd = os.getcwd()

    if not env:
        env = common_environment()

    cmd = list(cmd)

    escaped_cmd = ' '.join(cmd_quote(c) for c in cmd)

    display.info('Run command: %s' % escaped_cmd, verbosity=cmd_verbosity, truncate=True)
    display.info('Working directory: %s' % cwd, verbosity=2)

    program = find_executable(cmd[0], cwd=cwd, path=env['PATH'], required='warning')

    if program:
        display.info('Program found: %s' % program, verbosity=2)

    for key in sorted(env.keys()):
        display.info('%s=%s' % (key, env[key]), verbosity=2)

    if explain:
        return None, None

    communicate = False

    if stdin is not None:
        data = None
        communicate = True
    elif data is not None:
        stdin = subprocess.PIPE
        communicate = True

    if stdout:
        communicate = True

    if capture:
        stdout = stdout or subprocess.PIPE
        stderr = subprocess.PIPE
        communicate = True
    else:
        stderr = None

    start = time.time()
    process = None

    try:
        try:
            process = subprocess.Popen(cmd, env=env, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd)
        except OSError as ex:
            if ex.errno == errno.ENOENT:
                raise ApplicationError('Required program "%s" not found.' % cmd[0])
            raise

        if communicate:
            encoding = 'utf-8'
            if data is None or isinstance(data, bytes):
                data_bytes = data
            else:
                data_bytes = data.encode(encoding, 'surrogateescape')
            stdout_bytes, stderr_bytes = process.communicate(data_bytes)
            stdout_text = stdout_bytes.decode(encoding, str_errors) if stdout_bytes else u''
            stderr_text = stderr_bytes.decode(encoding, str_errors) if stderr_bytes else u''
        else:
            process.wait()
            stdout_text, stderr_text = None, None
    finally:
        if process and process.returncode is None:
            process.kill()
            display.info('')  # the process we're interrupting may have completed a partial line of output
            display.notice('Killed command to avoid an orphaned child process during handling of an unexpected exception.')

    status = process.returncode
    runtime = time.time() - start

    display.info('Command exited with status %s after %s seconds.' % (status, runtime), verbosity=4)

    if status == 0:
        return stdout_text, stderr_text

    raise SubprocessError(cmd, status, stdout_text, stderr_text, runtime)


def common_environment():
    """Common environment used for executing all programs."""
    env = dict(
        LC_ALL='en_US.UTF-8',
        PATH=os.environ.get('PATH', os.path.defpath),
    )

    required = (
        'HOME',
    )

    optional = (
        'HTTPTESTER',
        'LD_LIBRARY_PATH',
        'SSH_AUTH_SOCK',
        # MacOS High Sierra Compatibility
        # http://sealiesoftware.com/blog/archive/2017/6/5/Objective-C_and_fork_in_macOS_1013.html
        # Example configuration for macOS:
        # export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
        'OBJC_DISABLE_INITIALIZE_FORK_SAFETY',
        'ANSIBLE_KEEP_REMOTE_FILES',
        # MacOS Homebrew Compatibility
        # https://cryptography.io/en/latest/installation/#building-cryptography-on-macos
        # This may also be required to install pyyaml with libyaml support when installed in non-standard locations.
        # Example configuration for brew on macOS:
        # export LDFLAGS="-L$(brew --prefix openssl)/lib/     -L$(brew --prefix libyaml)/lib/"
        # export  CFLAGS="-I$(brew --prefix openssl)/include/ -I$(brew --prefix libyaml)/include/"
        # However, this is not adequate for PyYAML 3.13, which is the latest version supported on Python 2.6.
        # For that version the standard location must be used, or `pip install` must be invoked with additional options:
        # --global-option=build_ext --global-option=-L{path_to_lib_dir}
        'LDFLAGS',
        'CFLAGS',
    )

    env.update(pass_vars(required=required, optional=optional))

    return env


def pass_vars(required, optional):
    """
    :type required: collections.Iterable[str]
    :type optional: collections.Iterable[str]
    :rtype: dict[str, str]
    """
    env = {}

    for name in required:
        if name not in os.environ:
            raise MissingEnvironmentVariable(name)
        env[name] = os.environ[name]

    for name in optional:
        if name not in os.environ:
            continue
        env[name] = os.environ[name]

    return env


def deepest_path(path_a, path_b):
    """Return the deepest of two paths, or None if the paths are unrelated.
    :type path_a: str
    :type path_b: str
    :rtype: str | None
    """
    if path_a == '.':
        path_a = ''

    if path_b == '.':
        path_b = ''

    if path_a.startswith(path_b):
        return path_a or '.'

    if path_b.startswith(path_a):
        return path_b or '.'

    return None


def remove_tree(path):
    """
    :type path: str
    """
    try:
        shutil.rmtree(path)
    except OSError as ex:
        if ex.errno != errno.ENOENT:
            raise


def make_dirs(path):
    """
    :type path: str
    """
    try:
        os.makedirs(path)
    except OSError as ex:
        if ex.errno != errno.EEXIST:
            raise


def is_binary_file(path):
    """
    :type path: str
    :rtype: bool
    """
    assume_text = set([
        '.cfg',
        '.conf',
        '.crt',
        '.cs',
        '.css',
        '.html',
        '.ini',
        '.j2',
        '.js',
        '.json',
        '.md',
        '.pem',
        '.ps1',
        '.psm1',
        '.py',
        '.rst',
        '.sh',
        '.txt',
        '.xml',
        '.yaml',
        '.yml',
    ])

    assume_binary = set([
        '.bin',
        '.eot',
        '.gz',
        '.ico',
        '.iso',
        '.jpg',
        '.otf',
        '.p12',
        '.png',
        '.pyc',
        '.rpm',
        '.ttf',
        '.woff',
        '.woff2',
        '.zip',
    ])

    ext = os.path.splitext(path)[1]

    if ext in assume_text:
        return False

    if ext in assume_binary:
        return True

    with open(path, 'rb') as path_fd:
        return b'\0' in path_fd.read(1024)


def generate_password():
    """Generate a random password.
    :rtype: str
    """
    chars = [
        string.ascii_letters,
        string.digits,
        string.ascii_letters,
        string.digits,
        '-',
    ] * 4

    password = ''.join([random.choice(char) for char in chars[:-1]])

    display.sensitive.add(password)

    return password


class Display(object):
    """Manages color console output."""
    clear = '\033[0m'
    red = '\033[31m'
    green = '\033[32m'
    yellow = '\033[33m'
    blue = '\033[34m'
    purple = '\033[35m'
    cyan = '\033[36m'

    verbosity_colors = {
        0: None,
        1: green,
        2: blue,
        3: cyan,
    }

    def __init__(self):
        self.verbosity = 0
        self.color = True
        self.warnings = []
        self.warnings_unique = set()
        self.info_stderr = False
        self.rows = 0
        self.columns = 0
        self.truncate = 0
        self.redact = False
        self.sensitive = set()

        if os.isatty(0):
            self.rows, self.columns = unpack('HHHH', fcntl.ioctl(0, TIOCGWINSZ, pack('HHHH', 0, 0, 0, 0)))[:2]

    def __warning(self, message):
        """
        :type message: str
        """
        self.print_message('WARNING: %s' % message, color=self.purple, fd=sys.stderr)

    def review_warnings(self):
        """Review all warnings which previously occurred."""
        if not self.warnings:
            return

        self.__warning('Reviewing previous %d warning(s):' % len(self.warnings))

        for warning in self.warnings:
            self.__warning(warning)

    def warning(self, message, unique=False):
        """
        :type message: str
        :type unique: bool
        """
        if unique:
            if message in self.warnings_unique:
                return

            self.warnings_unique.add(message)

        self.__warning(message)
        self.warnings.append(message)

    def notice(self, message):
        """
        :type message: str
        """
        self.print_message('NOTICE: %s' % message, color=self.purple, fd=sys.stderr)

    def error(self, message):
        """
        :type message: str
        """
        self.print_message('ERROR: %s' % message, color=self.red, fd=sys.stderr)

    def info(self, message, verbosity=0, truncate=False):
        """
        :type message: str
        :type verbosity: int
        :type truncate: bool
        """
        if self.verbosity >= verbosity:
            color = self.verbosity_colors.get(verbosity, self.yellow)
            self.print_message(message, color=color, fd=sys.stderr if self.info_stderr else sys.stdout, truncate=truncate)

    def print_message(self, message, color=None, fd=sys.stdout, truncate=False):  # pylint: disable=locally-disabled, invalid-name
        """
        :type message: str
        :type color: str | None
        :type fd: file
        :type truncate: bool
        """
        if self.redact and self.sensitive:
            for item in self.sensitive:
                message = message.replace(item, '*' * len(item))

        if truncate:
            if len(message) > self.truncate > 5:
                message = message[:self.truncate - 5] + ' ...'

        if color and self.color:
            # convert color resets in message to desired color
            message = message.replace(self.clear, color)
            message = '%s%s%s' % (color, message, self.clear)

        if sys.version_info[0] == 2 and isinstance(message, type(u'')):
            message = message.encode('utf-8')

        print(message, file=fd)
        fd.flush()


class ApplicationError(Exception):
    """General application error."""
    pass


class ApplicationWarning(Exception):
    """General application warning which interrupts normal program flow."""
    pass


class SubprocessError(ApplicationError):
    """Error resulting from failed subprocess execution."""
    def __init__(self, cmd, status=0, stdout=None, stderr=None, runtime=None):
        """
        :type cmd: list[str]
        :type status: int
        :type stdout: str | None
        :type stderr: str | None
        :type runtime: float | None
        """
        message = 'Command "%s" returned exit status %s.\n' % (' '.join(cmd_quote(c) for c in cmd), status)

        if stderr:
            message += '>>> Standard Error\n'
            message += '%s%s\n' % (stderr.strip(), Display.clear)

        if stdout:
            message += '>>> Standard Output\n'
            message += '%s%s\n' % (stdout.strip(), Display.clear)

        message = message.strip()

        super(SubprocessError, self).__init__(message)

        self.cmd = cmd
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.runtime = runtime


class MissingEnvironmentVariable(ApplicationError):
    """Error caused by missing environment variable."""
    def __init__(self, name):
        """
        :type name: str
        """
        super(MissingEnvironmentVariable, self).__init__('Missing environment variable: %s' % name)

        self.name = name


def docker_qualify_image(name):
    """
    :type name: str
    :rtype: str
    """
    config = get_docker_completion().get(name, {})

    return config.get('name', name)


def parse_to_list_of_dict(pattern, value):
    """
    :type pattern: str
    :type value: str
    :return: list[dict[str, str]]
    """
    matched = []
    unmatched = []

    for line in value.splitlines():
        match = re.search(pattern, line)

        if match:
            matched.append(match.groupdict())
        else:
            unmatched.append(line)

    if unmatched:
        raise Exception('Pattern "%s" did not match values:\n%s' % (pattern, '\n'.join(unmatched)))

    return matched


def get_available_port():
    """
    :rtype: int
    """
    # this relies on the kernel not reusing previously assigned ports immediately
    socket_fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    with contextlib.closing(socket_fd):
        socket_fd.bind(('', 0))
        return socket_fd.getsockname()[1]


def get_subclasses(class_type):  # type: (t.Type[C]) -> t.Set[t.Type[C]]
    """Returns the set of types that are concrete subclasses of the given type."""
    subclasses = set()
    queue = [class_type]

    while queue:
        parent = queue.pop()

        for child in parent.__subclasses__():
            if child not in subclasses:
                if not inspect.isabstract(child):
                    subclasses.add(child)
                queue.append(child)

    return subclasses


def is_subdir(candidate_path, path):  # type: (str, str) -> bool
    """Returns true if candidate_path is path or a subdirectory of path."""
    if not path.endswith(os.sep):
        path += os.sep

    if not candidate_path.endswith(os.sep):
        candidate_path += os.sep

    return candidate_path.startswith(path)


def import_plugins(directory, root=None):  # type: (str, t.Optional[str]) -> None
    """
    Import plugins from the given directory relative to the given root.
    If the root is not provided, the 'lib' directory for the test runner will be used.
    """
    if root is None:
        root = os.path.dirname(__file__)

    path = os.path.join(root, directory)
    prefix = 'lib.%s.' % directory.replace(os.sep, '.')

    for (_, name, _) in pkgutil.iter_modules([path], prefix=prefix):
        module_path = os.path.join(root, name[4:].replace('.', os.sep) + '.py')
        load_module(module_path, name)


def load_plugins(base_type, database):  # type: (t.Type[C], t.Dict[str, t.Type[C]]) -> None
    """
    Load plugins of the specified type and track them in the specified database.
    Only plugins which have already been imported will be loaded.
    """
    plugins = dict((sc.__module__.split('.')[2], sc) for sc in get_subclasses(base_type))  # type: t.Dict[str, t.Type[C]]

    for plugin in plugins:
        database[plugin] = plugins[plugin]


def load_module(path, name):  # type: (str, str) -> None
    """Load a Python module using the given name and path."""
    if sys.version_info >= (3, 4):
        import importlib.util

        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sys.modules[name] = module
    else:
        import imp

        with open(path, 'r') as module_file:
            imp.load_module(name, module_file, path, ('.py', 'r', imp.PY_SOURCE))


display = Display()  # pylint: disable=locally-disabled, invalid-name
