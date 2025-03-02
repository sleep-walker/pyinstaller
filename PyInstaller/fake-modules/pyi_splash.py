# -----------------------------------------------------------------------------
# Copyright (c) 2005-2023, PyInstaller Development Team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#
# SPDX-License-Identifier: Apache-2.0
# -----------------------------------------------------------------------------

# This module is not a "fake module" in the classical sense, but a real module that can be imported. It acts as an RPC
# interface for the functions of the bootloader.
"""
This module connects to the bootloader to send messages to the splash screen.

It is intended to act as a RPC interface for the functions provided by the bootloader, such as displaying text or
closing. This makes the users python program independent of how the communication with the bootloader is implemented,
since a consistent API is provided.

To connect to the bootloader, it connects to a local tcp socket whose port is passed through the environment variable
'_PYI_SPLASH_IPC'. The bootloader creates a server socket and accepts every connection request. Since the os-module,
which is needed to request the environment variable, is not available at boot time, the module does not establish the
connection until initialization.

The protocol by which the Python interpreter communicates with the bootloader is implemented in this module.

This module does not support reloads while the splash screen is displayed, i.e. it cannot be reloaded (such as by
importlib.reload), because the splash screen closes automatically when the connection to this instance of the module
is lost.
"""

import atexit
import os

# Import the _socket module instead of the socket module. All used functions to connect to the ipc system are
# provided by the C module and the users program does not necessarily need to include the socket module and all
# required modules it uses.
import _socket

__all__ = ["CLOSE_CONNECTION", "FLUSH_CHARACTER", "is_alive", "close", "update_text"]

try:
    # The user might have excluded logging from imports.
    import logging as _logging
except ImportError:
    _logging = None

try:
    # The user might have excluded functools from imports.
    from functools import update_wrapper
except ImportError:
    update_wrapper = None


# Utility
def _log(level, msg, *args, **kwargs):
    """
    Conditional wrapper around logging module. If the user excluded logging from the imports or it was not imported,
    this function should handle it and avoid using the logger.
    """
    if _logging:
        logger = _logging.getLogger(__name__)
        logger.log(level, msg, *args, **kwargs)


# These constants define single characters which are needed to send commands to the bootloader. Those constants are
# also set in the tcl script.
CLOSE_CONNECTION = b'\x04'  # ASCII End-of-Transmission character
FLUSH_CHARACTER = b'\x0D'  # ASCII Carriage Return character

# Module internal variables
_initialized = False
# Keep these variables always synchronized
_ipc_socket_closed = True
_ipc_socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)


def _initialize():
    """
    Initialize this module

    :return:
    """
    global _initialized, _ipc_socket, _ipc_socket_closed

    # If _ipc_port is zero, the splash screen is intentionally suppressed (for example, we are in sub-process spawned
    # via sys.executable). Mark the splash screen as initialized, but do not attempt to connect.
    if _ipc_port == 0:
        _initialized = True
        return

    # Attempt to connect to the splash screen process.
    try:
        _ipc_socket.connect(("127.0.0.1", _ipc_port))
        _ipc_socket_closed = False

        _initialized = True
        _log(20, "IPC connection to the splash screen was successfully established.")  # log-level: info
    except OSError as err:
        raise ConnectionError(f"Could not connect to TCP port {_ipc_port}.") from err


# We expect a splash screen from the bootloader, but if _PYI_SPLASH_IPC is not set, the module cannot connect to it.
# _PYI_SPLASH_IPC being set to zero indicates that splash screen should be (gracefully) suppressed; i.e., the calls
# in this module should become no-op without generating warning messages.
try:
    _ipc_port = int(os.environ['_PYI_SPLASH_IPC'])
    del os.environ['_PYI_SPLASH_IPC']
    # Initialize the connection upon importing this module. This will establish a connection to the bootloader's TCP
    # server socket.
    _initialize()
except (KeyError, ValueError):
    # log-level: warning
    _log(
        30,
        "The environment does not allow connecting to the splash screen. Did bootloader fail to initialize it?",
        exc_info=True,
    )
except ConnectionError:
    # log-level: error
    _log(40, "Failed to connect to the bootloader's IPC server!", exc_info=True)


def _check_connection(func):
    """
    Utility decorator for checking whether the function should be executed.

    The wrapped function may raise a ConnectionError if the module was not initialized correctly.
    """
    def wrapper(*args, **kwargs):
        """
        Executes the wrapped function if the environment allows it.

        That is, if the connection to to bootloader has not been closed and the module is initialized.

        :raises RuntimeError: if the module was not initialized correctly.
        """
        if _initialized and _ipc_socket_closed:
            if _ipc_port != 0:
                _log(20, "Connection to splash screen has already been closed.")  # log-level: info
            return
        elif not _initialized:
            raise RuntimeError("This module is not initialized; did it fail to load?")

        return func(*args, **kwargs)

    if update_wrapper:
        # For runtime introspection
        update_wrapper(wrapper, func)

    return wrapper


@_check_connection
def _send_command(cmd, args=None):
    """
    Send the command followed by args to the splash screen.

    :param str cmd: The command to send. All command have to be defined as procedures in the tcl splash screen script.
    :param list[str] args: All arguments to send to the receiving function
    """
    if args is None:
        args = []

    full_cmd = "%s(%s)" % (cmd, " ".join(args))
    try:
        _ipc_socket.sendall(full_cmd.encode("utf-8") + FLUSH_CHARACTER)
    except OSError as err:
        raise ConnectionError(f"Unable to send command {full_cmd!r} to the bootloader") from err


def is_alive():
    """
    Indicates whether the module can be used.

    Returns False if the module is either not initialized or was disabled by closing the splash screen. Otherwise,
    the module should be usable.
    """
    return _initialized and not _ipc_socket_closed


@_check_connection
def update_text(msg: str):
    """
    Updates the text on the splash screen window.

    :param str msg: the text to be displayed
    :raises ConnectionError: If the OS fails to write to the socket.
    :raises RuntimeError: If the module is not initialized.
    """
    _send_command("update_text", [msg])


def close():
    """
    Close the connection to the ipc tcp server socket.

    This will close the splash screen and renders this module unusable. After this function is called, no connection
    can be opened to the splash screen again and all functions in this module become unusable.
    """
    global _ipc_socket_closed
    if _initialized and not _ipc_socket_closed:
        _ipc_socket.sendall(CLOSE_CONNECTION)
        _ipc_socket.close()
        _ipc_socket_closed = True


@atexit.register
def _exit():
    close()
