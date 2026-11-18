Licence texts that the bundled components require us to reproduce but that their wheels do not
ship. build/third_party_notices.py embeds them in THIRD-PARTY-NOTICES.txt. Provenance (fetched
2026-11-18, unmodified unless noted):

qt/        github.com/qt/qtbase, tag v6.10.2, src/3rdparty/:
             freetype/docs/FTL.TXT (Qt uses FreeType under the FTL), harfbuzz-ng/COPYING,
             libpng/LICENSE, pcre2/LICENCE.md, double-conversion/LICENSE, md4c/LICENSE.md
winrt/     github.com/pywinrt/pywinrt, main, LICENSE (covers every winrt-* package)
wmi/       composed: WMI ships no licence file; the copyright and licence statement are quoted
           from wmi.py 1.5.1, followed by the standard MIT text it references
openssl/   github.com/openssl/openssl, tag openssl-3.5.9, LICENSE.txt (Apache-2.0)
python/    github.com/python/cpython, tag v3.13.17:
             incorporated-software.txt = Doc/license.rst from "Licenses and Acknowledgements for
               Incorporated Software" to the end (expat, libmpdec, mimalloc, zlib, libffi, ...)
             hacl-LICENSE.txt = the MIT header of Modules/_hacl/Hacl_Hash_SHA2.c (comment markers
               removed)
           github.com/python/cpython-source-deps, tag xz-5.2.5 (PCbuild/get_externals.bat), COPYING

Refresh these when Qt, Python or OpenSSL moves to a new line (e.g. Qt 6.11, Python 3.14).
