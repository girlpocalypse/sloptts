# Switch Orca from Speech Dispatcher/espeak-ng to Spiel (Fedora Silverblue 44)

## Context
The user wants Orca to speak through Spiel instead of espeak-ng via Speech Dispatcher. Orca 50.2 already includes Spiel support in `/usr/lib/python3.14/site-packages/orca/spiel.py`. That file calls `gi.require_version("Spiel", "1.0")`, and Orca hides the Spiel option when this fails. It fails today because libspiel is not installed.

Research findings:
- Fedora has no libspiel package (the Fedora package API has no entry for it).
- No COPR project packages it (checked through the COPR search API).
- The user has no BlueBuild recipe, so the fix must be layered onto the current image.
- libspiel 1.0.5 builds with meson. It pulls in its libspeechprovider dependency automatically (a meson subproject fallback). By default it builds the introspection typelib that Orca's Python code needs.
- Voice providers come as Flatpaks from the Spiel project's own Flatpak repository, not from Flathub.

## Scope for this session (the user's instruction)
Carry out steps 1–3 only: set up the distrobox, write the spec, and build the RPM. **Do not install the RPM anywhere**: no `rpm-ostree install`, and no `dnf install` of the built RPM, even inside the distrobox. Do not add the Flatpak remote or change Orca settings. Stop after reporting the paths of the built RPMs and their file lists (`rpm -qlp`).

## Recommended approach: build a libspiel RPM in a distrobox, then layer it
Layering an RPM puts the library and typelib in `/usr/lib64`. Orca, which runs on the host, then finds them with no environment changes. rpm-ostree re-applies local layered RPMs on every image update.

1. **Create a Fedora 44 distrobox (pinned to :44 so the build matches the host; existing boxes use `:latest`)** with `distrobox create -n spiel-build -i registry.fedoraproject.org/fedora-toolbox:44`. Install the build tools there: `rpm-build meson gcc gobject-introspection-devel glib2-devel gstreamer1-devel gstreamer1-plugins-base-devel`. Leave out docs (`-Ddocs=false`) and tests (`-Dtests=false`) to keep the dependencies small.
2. **Write `~/rpmbuild/SPECS/libspiel.spec`:**
   - Source0 is the v1.0.5 tag tarball. Because the build fetches the libspeechprovider subproject, run `meson subprojects download` before packaging, or add a Source1 for it.
   - Use the `%meson`, `%meson_build` and `%meson_install` macros with `-Ddocs=false -Dtests=false`.
   - `%files` should cover `libspiel-1.0.so.*`, `girepository-1.0/Spiel-1.0.typelib`, the bundled libspeechprovider library and its typelib, and the `spiel` CLI utility.
3. **Build** with `distrobox enter spiel-build -- rpmbuild -ba ...`.
4. **Layer it on the host:** `rpm-ostree install ~/rpmbuild/RPMS/x86_64/libspiel-1.0.5-*.rpm`, then reboot.
5. **Install a voice provider:**
   - `flatpak remote-add --user spiel https://project-spiel.org/flatpaks/spiel-repo.flatpakrepo`
   - `flatpak install --user spiel org.espeak.Speech.Provider` for the same voice, or `ai.piper.Speech.Provider` for neural voices.
6. **Switch Orca:** run `orca --replace --speech-system=spiel`, or choose Orca Preferences → Speech → Speech system → Spiel and save so it persists.

The spec file (step 2) is where the user can write a part themselves: the `%files` section and the handling of the subproject source.

## Fallback without a reboot or layering
Build with `--prefix=/usr/local` inside the distrobox and install into `/usr/local`, which is writable on Silverblue. Then two things are needed:
- A `/etc/ld.so.conf.d/local.conf` entry for `/usr/local/lib64`, followed by `sudo ldconfig`.
- `GI_TYPELIB_PATH=/usr/local/lib64/girepository-1.0` set in `~/.config/environment.d/spiel.conf`, so Orca sees it at login.

This is more fragile: the library is built against a container, and there are two separate path hacks.

## Verification
- `python3 -c "import gi; gi.require_version('Spiel','1.0'); from gi.repository import Spiel; print([v.props.name for v in Spiel.Speaker.new_sync(None).props.voices])"` prints the provider's voices.
- Spiel appears in Orca's "Speech system" dropdown, and Orca speaks through it after `orca --replace`.
- Rollback: switch Orca back to Speech Dispatcher, then run `rpm-ostree uninstall libspiel` (or `rpm-ostree rollback`).
