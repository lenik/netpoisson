project(
    'netpoisson',
    version: run_command(
        'sh',
        '-c', '''
        v=$(
            command -v zfr >/dev/null 2>&1 && zfr version 2>/dev/null || true
        )
        if [ -z "$v" ] && [ -f VERSION ]; then
            v=$(head -n1 VERSION | tr -d '\r')
            v=${v#v}
        fi
        if [ -z "$v" ]; then
            v="0.0.0" # FIXED TO 0.0.0, DO NOT MODIFY
        fi
        printf '%s' "$v"
        ''',
        check: false,
    ).stdout().strip(),
    license: 'AGPL-3.0-or-later',
)

project_author = 'Lenik'
project_email = 'netpoisson@bodz.net'
project_year = 2026

py = import('python').find_installation('python3')
fs = import('fs')
asciidoctor = find_program('asciidoctor', required: true)

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
localedir = prefix / get_option('localedir')
mandir = prefix / get_option('mandir')
# Private modules — keep bindir to the bash launcher only.
pkglibdir = prefix / 'lib' / meson.project_name()
pkgdocdir = datadir / 'doc' / meson.project_name()

subdir('po')

# --- Launcher: bindir/netpoisson (bash from netpoisson.in) ---

# Uninstalled / build-tree launcher → src/ + build po/
conf_local = configuration_data()
conf_local.set_quoted('PYTHON', py.full_path())
conf_local.set_quoted('PKGLIBDIR', meson.project_source_root() / 'src')
conf_local.set_quoted('LOCALEDIR', meson.project_build_root() / 'po')
netpoisson_local_cfg = configure_file(
    input: 'netpoisson.in',
    output: 'netpoisson.local.in',
    configuration: conf_local,
)
custom_target(
    'netpoisson',
    input: netpoisson_local_cfg,
    output: 'netpoisson',
    command: [
        'sh',
        '-c',
        'cp -f "$1" "$2" && chmod 755 "$2"',
        'sh',
        '@INPUT@',
        '@OUTPUT@',
    ],
    build_by_default: true,
)

# Installed launcher → /usr/lib/netpoisson
conf_inst = configuration_data()
conf_inst.set_quoted('PYTHON', '/usr/bin/python3')
conf_inst.set_quoted('PKGLIBDIR', pkglibdir)
conf_inst.set_quoted('LOCALEDIR', localedir)
netpoisson_installed = configure_file(
    input: 'netpoisson.in',
    output: 'netpoisson.install',
    configuration: conf_inst,
)
install_data(
    netpoisson_installed,
    install_dir: bindir,
    rename: 'netpoisson',
    install_mode: 'rwxr-xr-x',
)

# --- Python tree under lib/netpoisson (not bindir) ---

foreach mod : [
    'netpoisson.py',
    'i18nutil.py',
    'protocol.py',
    'netio.py',
    'netio_sock.py',
    'peer_server.py',
    'webui.py',
    'web_page.html',
    'web_page.css',
    'web_dash.js',
    'web_raw3d.js',
    'wiresec.py',
]
    install_data(
        'src' / mod,
        install_dir: pkglibdir,
    )
endforeach

foreach pkg : ['traffic', 'peer_client', 'npcli']
    install_subdir(
        'src' / pkg,
        install_dir: pkglibdir / pkg,
        strip_directory: true,
        exclude_directories: ['__pycache__'],
        exclude_files: ['*.pyc'],
    )
endforeach

install_subdir(
    'src' / 'web_themes',
    install_dir: pkglibdir / 'web_themes',
    strip_directory: true,
)

install_subdir(
    'src' / 'web_i18n',
    install_dir: pkglibdir / 'web_i18n',
    strip_directory: true,
)

install_data(
    'VERSION',
    install_dir: pkglibdir,
)

install_data(
    'netpoisson.bash',
    install_dir: datadir / 'bash-completion' / 'completions',
    rename: 'netpoisson',
)

install_data(
    [
        'LICENSE',
        'README.md',
        'README-zh_CN.md',
    ],
    install_dir: pkgdocdir,
)

test(
    'python-unit',
    py,
    args: ['-m', 'unittest', 'discover', '-s', (meson.project_source_root() / 'tests'), '-p', 'test_*.py'],
    env: [
        'PYTHONPATH=' + (meson.project_source_root() / 'src'),
        'NETPOISSON_LOCALEDIR=' + (meson.project_build_root() / 'po'),
    ],
)

run_target(
    'posync',
    command: [
        find_program('zfr'),
        'translate',
        '--sync',
        '-C', meson.project_source_root(),
    ],
)

run_target(
    'look',
    command: [
        find_program('zfr'),
        'build',
        '--look',
        '-C', meson.project_source_root(),
        '-b', meson.project_build_root(),
    ],
)

run_target(
    'install-symlinks',
    command: [
        'bash',
        meson.project_source_root() / 'scripts' / 'install-symlinks.sh',
        meson.project_source_root(),
        meson.project_build_root(),
    ],
)

run_target(
    'uninstall-symlinks',
    command: [
        'bash',
        meson.project_source_root() / 'scripts' / 'uninstall-symlinks.sh',
        meson.project_source_root(),
        meson.project_build_root(),
    ],
)

puffs = [
    'netpoisson',
]
foreach puff : puffs
  custom_target(
    puff + '-man',
    input: 'man' / (puff + '.adoc'),
    output: puff + '.1',
    command: [
      asciidoctor,
      '-b', 'manpage',
      '-a', 'project-version=' + meson.project_version(),
      '-a', 'project-year=@0@'.format(project_year),
      '-a', 'project-author=' + project_author,
      '-a', 'project-email=' + project_email,
      '-o', '@OUTPUT@',
      '@INPUT@',
    ],
    build_by_default: true,
    install: true,
    install_dir: mandir / 'man1',
  )
endforeach

# Whole-document translated man pages under man/<locale>/.
man_i18n_langs = []
foreach lang : ['ar', 'de', 'es', 'fr', 'it', 'ja', 'ko', 'zh_CN']
  if fs.is_dir(meson.project_source_root() / 'man' / lang)
    man_i18n_langs += lang
  endif
endforeach
_sh = find_program('sh')
foreach lang : man_i18n_langs
  foreach puff : puffs
    adoc = meson.project_source_root() / 'man' / lang / (puff + '.adoc')
    if fs.is_file(adoc)
      _loc_man = custom_target(
        puff + '-man-' + lang,
        input: adoc,
        output: puff + '-' + lang + '.1',
        command: [
          asciidoctor,
          '-b', 'manpage',
          '-a', 'project-version=' + meson.project_version(),
          '-a', 'project-year=@0@'.format(project_year),
          '-a', 'project-author=' + project_author,
          '-a', 'project-email=' + project_email,
          '-o', '@OUTPUT@',
          '@INPUT@',
        ],
        build_by_default: true,
      )
      # Honor DESTDIR / MESON_INSTALL_DESTDIR_PREFIX (do not install into /usr directly).
      meson.add_install_script(
        _sh,
        '-c',
        'base="${MESON_INSTALL_DESTDIR_PREFIX:-${DESTDIR:-}${MESON_INSTALL_PREFIX:-}}"; dest="$base/$0"; mkdir -p "$(dirname "$dest")"; install -m644 "$1" "$dest"',
        get_option('mandir') / lang / 'man1' / (puff + '.1'),
        _loc_man,
      )
    endif
  endforeach
endforeach
