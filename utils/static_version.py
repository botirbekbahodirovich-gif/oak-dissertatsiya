"""STATIC_VERSION — cache-buster for /static/ CSS and JS.

nginx serves /static/ with ``expires 7d``, so a CSS/JS fix reaches browsers that
already hold the old file a week late unless the URL changes. Templates append
``?v={{ STATIC_VERSION }}`` to every local CSS/JS URL; this module decides the
value once, at start-up, from the commit that was built:

1. ``STATIC_VERSION`` environment variable (explicit override);
2. ``BUILD_VERSION`` file at the project root. ``git archive`` expands the
   ``$Format:%H$`` placeholder in it (``export-subst`` in .gitattributes), so a
   tree deployed from an archive carries its commit SHA without a .git folder;
3. ``git rev-parse HEAD`` when the project root is a checkout;
4. a digest of the size and mtime of static/css and static/js files, so a tree
   with neither of the above still changes URL whenever an asset changes.

The value goes straight into HTML attributes, so only ``[A-Za-z0-9._-]`` is
ever returned.
"""
import hashlib
import os
import re
import subprocess

SHA_LENGTH = 10
FALLBACK = 'dev'

_SAFE_VALUE = re.compile(r'^[A-Za-z0-9._-]{1,40}$')
_HEX_SHA = re.compile(r'^[0-9a-f]{7,40}$')
_ASSET_SUFFIXES = ('.css', '.js', '.mjs')


def _from_environment(root, environ):
    value = (environ.get('STATIC_VERSION') or '').strip()
    return value if _SAFE_VALUE.match(value) else None


def _from_build_file(root, environ):
    try:
        with open(os.path.join(root, 'BUILD_VERSION'), encoding='ascii') as fh:
            value = fh.read().strip().lower()
    except (OSError, UnicodeDecodeError):
        return None
    # An archive made without export-subst leaves the literal "$Format:%H$".
    return value[:SHA_LENGTH] if _HEX_SHA.match(value) else None


def _from_git(root, environ):
    if not os.path.exists(os.path.join(root, '.git')):
        return None
    try:
        result = subprocess.run(['git', '-C', root, 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip().lower()
    if result.returncode == 0 and _HEX_SHA.match(value):
        return value[:SHA_LENGTH]
    return None


def _from_assets(root, environ):
    digest = hashlib.sha1()
    found = False
    for sub in ('css', 'js'):
        folder = os.path.join(root, 'static', sub)
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.endswith(_ASSET_SUFFIXES):
                continue
            try:
                info = os.stat(os.path.join(folder, name))
            except OSError:
                continue
            digest.update(('%s/%s:%d:%d;' % (sub, name, info.st_size, info.st_mtime_ns)).encode())
            found = True
    return digest.hexdigest()[:SHA_LENGTH] if found else None


_SOURCES = (
    ('environment', _from_environment),
    ('build-file', _from_build_file),
    ('git', _from_git),
    ('assets', _from_assets),
)


def describe_static_version(root, environ=None):
    """Return ``(value, source)``; ``source`` names which rule produced the value."""
    environ = os.environ if environ is None else environ
    for source, finder in _SOURCES:
        value = finder(root, environ)
        if value:
            return value, source
    return FALLBACK, 'fallback'


def resolve_static_version(root, environ=None):
    return describe_static_version(root, environ)[0]
