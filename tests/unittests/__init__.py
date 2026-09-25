# cicwave unit tests

#- Run hermetically: plugins installed in this environment must not
#- change what the tests see (tests that exercise plugins pass their own).
import os

os.environ.setdefault("CICWAVE_PLUGINS", "0")
