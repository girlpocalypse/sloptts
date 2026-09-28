%global spiel_tag SPIEL_1_0_4
%global sp_version 1.0.3
%global sp_tag SPEECHPROVIDER_1_0_3

Name:           libspiel
Version:        1.0.4
Release:        1%{?dist}
Summary:        Speech synthesis client library for the Spiel D-Bus speech framework

License:        LGPL-2.1-or-later
URL:            https://project-spiel.org/
Source0:        https://github.com/project-spiel/libspiel/archive/refs/tags/%{spiel_tag}.tar.gz#/libspiel-%{version}.tar.gz
# Bundled as a meson subproject; not packaged in Fedora
Source1:        https://github.com/project-spiel/libspeechprovider/archive/refs/tags/%{sp_tag}.tar.gz#/libspeechprovider-%{sp_version}.tar.gz

BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  pkgconfig(gio-2.0) >= 2.76
BuildRequires:  pkgconfig(gio-unix-2.0)
BuildRequires:  pkgconfig(gstreamer-1.0)
BuildRequires:  pkgconfig(gstreamer-audio-1.0)
BuildRequires:  gobject-introspection-devel

Provides:       bundled(libspeechprovider) = %{sp_version}

%description
libspiel is the client library for Spiel, a D-Bus based speech synthesis
framework. It lets applications such as the Orca screen reader speak through
Spiel speech providers (for example eSpeak-NG or Piper). This package bundles
libspeechprovider %{sp_version}.

%package devel
Summary:        Development files for %{name}
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description devel
Headers, pkg-config files and GObject introspection data for %{name}.

%prep
%autosetup -n libspiel-%{spiel_tag}
mkdir -p subprojects/libspeechprovider
tar xzf %{SOURCE1} -C subprojects/libspeechprovider --strip-components=1

%build
%meson \
    -Ddocs=false \
    -Dtests=false \
    -Dutils=true \
    -Dlibspeechprovider:docs=false \
    -Dlibspeechprovider:tests=false \
    -Dlibspeechprovider:introspection=true
%meson_build

%install
%meson_install

%files
%license COPYING
%doc README.md
%{_bindir}/spiel
%{_libdir}/libspiel-1.0.so.1{,.*}
# Upstream ships this library without a versioned soname; libspiel links to it directly
%{_libdir}/libspeech-provider-1.0.so
%{_libdir}/girepository-1.0/Spiel-1.0.typelib
%{_libdir}/girepository-1.0/SpeechProvider-1.0.typelib
%{_datadir}/glib-2.0/schemas/org.monotonous.libspiel.gschema.xml

%files devel
%{_includedir}/spiel/
%{_includedir}/speech-provider/
%{_libdir}/libspiel-1.0.so
%{_libdir}/pkgconfig/spiel-1.0.pc
%{_libdir}/pkgconfig/speech-provider-1.0.pc
%{_datadir}/gir-1.0/Spiel-1.0.gir
%{_datadir}/gir-1.0/SpeechProvider-1.0.gir
%{_datadir}/speech-provider/

%changelog
* Thu Sep 24 2026 Local Build <noreply@localhost> - 1.0.4-1
- Local build of libspiel 1.0.4 with bundled libspeechprovider 1.0.3
