######################################################################
##        Copyright (c) 2020 Carsten Wulff Software, Norway
## ###################################################################
## Created       : wulff at 2020-10-24
## ###################################################################
##  The MIT License (MIT)
##
##  Permission is hereby granted, free of charge, to any person obtaining a copy
##  of this software and associated documentation files (the "Software"), to deal
##  in the Software without restriction, including without limitation the rights
##  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
##  copies of the Software, and to permit persons to whom the Software is
##  furnished to do so, subject to the following conditions:
##
##  The above copyright notice and this permission notice shall be included in all
##  copies or substantial portions of the Software.
##
##  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
##  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
##  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
##  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
##  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
##  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
##  SOFTWARE.
##
######################################################################

import logging

logger = logging.getLogger("cicwave")


class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: '\033[36m',
        logging.INFO: '\033[32m',
        logging.WARNING: '\033[93m',
        logging.ERROR: '\033[31m',
        logging.CRITICAL: '\033[31m',
    }
    RESET = '\033[0m'

    def format(self, record):
        msg = super().format(record)
        color = self.COLORS.get(record.levelno, '')
        return f"{color}{msg}{self.RESET}"


def setup_logging(color=True, level=logging.INFO):
    """Configure the cicwave logger with optional colored output."""
    log = logging.getLogger("cicwave")
    log.setLevel(level)
    log.handlers.clear()

    handler = logging.StreamHandler()
    handler.setLevel(level)

    if color:
        formatter = ColoredFormatter("%(message)s")
    else:
        formatter = logging.Formatter("%(levelname)-7s | %(message)s")

    handler.setFormatter(formatter)
    log.addHandler(handler)


class Command:
    """Minimal base command class for cicwave."""
    
    def __init__(self):
        pass