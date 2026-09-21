# Version is injected by packaging/rpm/Makefile via `zfr version`.
# RPM Version cannot contain '-'; use `zfr version -r` (hyphens → '_').
# srcversion is the unsanitized Meson/git version and names the tarball.
%{!?version:%global version 0.0.0}
%{!?srcversion:%global srcversion %{version}}

%global debug_package %{nil}

Name:           netpoisson
Version:        %{version}
Release:        1%{?dist}
Summary:        Poisson request generator and echo server

License:        AGPL-3.0-or-later
BuildArch:      noarch
URL:            https://github.com/lenik/netpoisson
Packager:       Lenik (谢继雷) <netpoisson@bodz.net>
Source0:        %{name}-%{srcversion}.tar.xz

BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  python3
BuildRequires:  gettext
BuildRequires:  asciidoctor

Requires:       python3

%description
netpoisson offers a Poisson stream of ECHO and STATUS requests, or listens
for them. It draws scrolling per-slice timelines and queue depths, can print
a JSON report, and can serve a live dashboard.

%prep
%setup -q -n %{name}-%{srcversion}

%build
meson setup build \
    --prefix=%{_prefix} \
    --bindir=%{_bindir} \
    --datadir=%{_datadir} \
    --mandir=%{_mandir} \
    --sysconfdir=%{_sysconfdir} \
    --localstatedir=%{_localstatedir} \
    --buildtype=plain
meson compile -C build

%install
meson install -C build --destdir=%{buildroot}

%files
%{_bindir}/netpoisson
%{_bindir}/i18nutil.py
%{_bindir}/protocol.py
%{_bindir}/netio.py
%{_bindir}/netio_sock.py
%{_bindir}/peer_server.py
%{_bindir}/webui.py
%{_bindir}/wiresec.py
%{_bindir}/traffic/
%{_bindir}/peer_client/
%{_bindir}/npcli/
%{_datadir}/bash-completion/completions/netpoisson
%{_mandir}/man1/netpoisson.1*
%{_mandir}/*/man1/netpoisson.1*
%{_datadir}/locale/*/LC_MESSAGES/netpoisson.mo
%{_datadir}/doc/netpoisson/
%changelog
* Mon Sep 21 2026 Lenik <netpoisson@bodz.net>
- TLS on TCP (--tls), PSK seal on UDP, modular packages, web :3871.
* Thu Aug 20 2026 Lenik <netpoisson@bodz.net>
- Align spec with debian/control (Meson, AGPL-3.0-or-later).
- Version comes from `zfr version`, the same method meson.build uses.
