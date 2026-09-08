#!/usr/bin/env bash
# Hermetic behavior tests for install-ios-local.sh. xcodebuild, xcrun and
# codesign are all faked; this file never builds, signs, or talks to a device.
#
# The case that matters is the CloudKit environment check. A Debug build signed
# with a development profile reads the *Development* container unless the
# entitlement is pinned, and nothing errors -- the app just shows "Waiting for
# First Publish" forever while the Mac publishes to Production. If that guard is
# ever "simplified" away, or moved to reading the entitlements file in the repo
# instead of the signed product, this is the test that notices.
#
# The pin lives in project.yml, not in GradusiOS.entitlements -- that file is
# XcodeGen output. An earlier hand edit to it passed this file and was then
# wiped by the gate's own `xcodegen generate` a few legs later, so the first
# assertions below read the YAML source and only then check that the generated
# file agrees.
#
# Every negative assertion is written `if run_install ...; then fail; fi`. The
# footgun is not the `&&` form -- verified on this host, `cmd && fail` and
# `out="$(cmd)" && fail` both survive `set -e`, because a command in a `&&` list
# is exempt from errexit. What does abort the file is a *bare* failing call or
# assignment: `out="$(run_install ...)"` with no `if` and no `||` exits
# immediately, taking the rest of the tests with it. The `if` form is used here
# because it is unambiguous at a glance, not because `&&` is broken.
# (test-install-mac-local.sh:12 still states the older, incorrect rationale for
# the same shape; corrected there in the same change.)
set -euo pipefail

umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SCRIPT="$SCRIPT_DIR/install-ios-local.sh"
ENTITLEMENTS="$SCRIPT_DIR/GradusiOS/GradusiOS.entitlements"
PROJECT_YML="$SCRIPT_DIR/project.yml"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/gradus-ios-install-tests.XXXXXX")"
FAKE_BIN="$TEST_ROOT/bin"
trap 'rm -rf "$TEST_ROOT"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() { echo "  ok: $*"; }

# --- project.yml must pin the environment -------------------------------------
# install-ios-local.sh verifies the signed product, not the source. That is the
# right place for the guard, but it only ever passes if the source pins the
# value in the first place, so pin the source here too.
#
# project.yml is that source. GradusiOS.entitlements is XcodeGen *output*, and
# checking only the output makes this assertion order-dependent: a hand edit to
# the generated file passes here and is silently reverted by the next `xcodegen
# generate` -- which is exactly what the gate does, a few legs after running
# this file. So assert the YAML first, then that the generated file agrees.
# Read the YAML through the project's managed environment, like every sibling
# suite (`uv run pytest`). A bare `python3` resolves to whichever interpreter is
# first on PATH -- on this machine homebrew's, which happens to have PyYAML,
# while /usr/bin/python3 does not. That made a gate-registered test depend on
# PATH ordering rather than on the repo.
PY_RUN=(uv run --quiet python)
"${PY_RUN[@]}" -c 'import yaml' 2>/dev/null ||
  fail "the project environment has no PyYAML; run 'uv sync' (these assertions read project.yml directly)"

yaml_env="$("${PY_RUN[@]}" - "$PROJECT_YML" <<'PYEOF'
import sys, yaml
spec = yaml.safe_load(open(sys.argv[1]))
props = spec["targets"]["GradusiOS"]["entitlements"]["properties"]
print(props.get("com.apple.developer.icloud-container-environment", ""))
PYEOF
)"
[[ "$yaml_env" == "Production" ]] ||
  fail "project.yml GradusiOS CloudKit environment is '${yaml_env:-<unset>}', expected Production (this is the source of truth; the .entitlements file is generated from it)"
pass "project.yml pins the Production container for GradusiOS"

# The widget has no CloudKit entitlement, so the key would be meaningless there
# -- and a stray copy is how a future edit ends up pinning the wrong target.
widget_env="$("${PY_RUN[@]}" - "$PROJECT_YML" <<'PYEOF'
import sys, yaml
spec = yaml.safe_load(open(sys.argv[1]))
props = spec["targets"]["GradusWidget"]["entitlements"]["properties"]
print(props.get("com.apple.developer.icloud-container-environment", ""))
PYEOF
)"
[[ -z "$widget_env" ]] ||
  fail "project.yml pins a CloudKit environment on GradusWidget ('$widget_env'); the widget has no CloudKit entitlement"
pass "the widget carries no CloudKit environment pin"

actual_env="$(/usr/bin/plutil -extract 'com\.apple\.developer\.icloud-container-environment' raw -o - "$ENTITLEMENTS" 2>/dev/null || true)"
[[ "$actual_env" == "Production" ]] ||
  fail "GradusiOS.entitlements CloudKit environment is '${actual_env:-<unset>}', expected Production -- project.yml and the generated file disagree; run \`xcodegen generate\`"
pass "the generated entitlements file agrees with project.yml"

# --- Fakes --------------------------------------------------------------------
mkdir -p "$FAKE_BIN"

# Written by each test to steer the fakes. Kept in files rather than exported
# variables so the script under test cannot accidentally see them as config.
STATE="$TEST_ROOT/state"
mkdir -p "$STATE"

write_state() { printf '%s' "$2" >"$STATE/$1"; }

cat >"$FAKE_BIN/xcodebuild" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" >>"$STATE/xcodebuild.args"
if [[ "$(cat "$STATE/xcodebuild_fails" 2>/dev/null || true)" == "1" ]]; then
  echo "** BUILD FAILED **" >&2
  exit 65
fi
# Mirror the layout the real build produces so the script can find the bundle.
derived=""
config="Debug"
prev=""
for arg in "$@"; do
  [[ "$prev" == "-derivedDataPath" ]] && derived="$arg"
  [[ "$prev" == "-configuration" ]] && config="$arg"
  prev="$arg"
done
if [[ "$(cat "$STATE/skip_bundle" 2>/dev/null || true)" != "1" ]]; then
  mkdir -p "$derived/Build/Products/$config-iphoneos/GradusiOS.app/PlugIns/GradusWidget.appex"
fi
echo "** BUILD SUCCEEDED **"
EOF

cat >"$FAKE_BIN/xcodegen" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$STATE/xcodegen.calls"
if [[ "$(cat "$STATE/xcodegen_fails" 2>/dev/null || true)" == "1" ]]; then
  echo "fake xcodegen: spec is invalid" >&2
  exit 1
fi
EOF

cat >"$FAKE_BIN/codesign" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# A codesign that cannot read the bundle is a different failure from a bundle
# whose entitlements are wrong, and must not be reported as the latter.
if [[ "$(cat "$STATE/codesign_fails" 2>/dev/null || true)" == "1" ]]; then
  echo "fake codesign: unable to read $*" >&2
  exit 1
fi
# The nastier variant: a full, parseable, *correct-looking* plist on stdout and
# a nonzero exit. Swallowing the pipeline status would accept this.
if [[ "$(cat "$STATE/codesign_fails_after_output" 2>/dev/null || true)" == "1" ]]; then
  printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>com.apple.developer.icloud-container-environment</key><string>Production</string></dict></plist>\n'
  exit 1
fi
# The last argument is the bundle codesign was pointed at; the widget's
# entitlements are steered separately so a leak into it can be tested.
if [[ "${!#}" == *.appex ]]; then
  widget_env="$(cat "$STATE/widget_env" 2>/dev/null || true)"
  if [[ -n "$widget_env" ]]; then
    printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>com.apple.developer.icloud-container-environment</key><string>%s</string></dict></plist>\n' "$widget_env"
  else
    printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>com.apple.security.application-groups</key><array><string>group.com.zerodelta.gradus</string></array></dict></plist>\n'
  fi
  exit 0
fi
env_value="$(cat "$STATE/signed_env" 2>/dev/null || true)"
if [[ "$env_value" == "MULTI" ]]; then
  printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>com.apple.developer.icloud-container-environment</key><array><string>Production</string><string>Development</string></array></dict></plist>\n'
  exit 0
fi
if [[ -z "$env_value" ]]; then
  # An unset entitlement is a real case: the product simply has no such key.
  printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>application-identifier</key><string>X.com.zerodelta.gradus.ios</string></dict></plist>\n'
  exit 0
fi
printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>com.apple.developer.icloud-container-environment</key><string>%s</string></dict></plist>\n' "$env_value"
EOF

cat >"$FAKE_BIN/xcrun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$STATE/xcrun.calls"
case "$*" in
  *"device install app"*)
    if [[ "$(cat "$STATE/install_fails" 2>/dev/null || true)" == "1" ]]; then
      echo "ERROR: The developer disk image could not be mounted on this device." >&2
      exit 1
    fi
    echo "App installed:"
    ;;
  *"device process launch"*)
    echo "Launched application with com.zerodelta.gradus.ios bundle identifier."
    ;;
  *"list devices"*)
    echo "Name Identifier State Model"
    ;;
esac
EOF

chmod +x "$FAKE_BIN"/*

run_install() {
  rm -f "$STATE/xcodebuild.args" "$STATE/xcrun.calls"
  env PATH="$FAKE_BIN:$PATH" STATE="$STATE" \
    XCODEBUILD="$FAKE_BIN/xcodebuild" \
    XCRUN="$FAKE_BIN/xcrun" \
    CODESIGN="$FAKE_BIN/codesign" \
    XCODEGEN="$FAKE_BIN/xcodegen" \
    GRADUS_IOS_DERIVED_DATA="$TEST_ROOT/dd" \
    bash "$INSTALL_SCRIPT" "$@"
}

reset_state() {
  rm -rf "$STATE" "$TEST_ROOT/dd"
  mkdir -p "$STATE"
  write_state signed_env "Production"
}

# --- A Development container must never reach a device ------------------------
reset_state
write_state signed_env "Development"
if output="$(run_install --device TESTDEVICE 2>&1)"; then
  fail "installed a bundle signed for the Development container"
fi
grep -q "Development" <<<"$output" ||
  fail "refusal did not name the environment it found: $output"
[[ ! -s "$STATE/xcrun.calls" ]] ||
  fail "refused the bundle but still called devicectl: $(cat "$STATE/xcrun.calls")"
pass "refuses to install a Development-container build, before touching the device"

# --- A missing entitlement is a refusal, not a pass ---------------------------
# The `|| true` around the plutil extraction in the script means an absent key
# yields an empty string; an empty string must not compare equal to anything.
reset_state
write_state signed_env ""
if run_install --device TESTDEVICE >/dev/null 2>&1; then
  fail "installed a bundle with no CloudKit environment entitlement at all"
fi
pass "refuses to install when the entitlement is absent"

# --- The happy path installs and launches -------------------------------------
reset_state
output="$(run_install --device TESTDEVICE 2>&1)" || fail "clean run failed: $output"
grep -q "device install app" "$STATE/xcrun.calls" || fail "never installed"
grep -q "device process launch" "$STATE/xcrun.calls" || fail "never launched"
grep -q -- "-allowProvisioningUpdates" "$STATE/xcodebuild.args" ||
  fail "build did not allow provisioning updates"
grep -q -- "-allowProvisioningDeviceRegistration" "$STATE/xcodebuild.args" ||
  fail "build did not allow device registration"
# Without these the suite survives a -destination swapped to a simulator, a
# default configuration flipped to Release, or the Mac's bundle id.
grep -q "platform=iOS" "$STATE/xcodebuild.args" ||
  fail "build did not target a physical iOS device"
grep -q "^Debug$" "$STATE/xcodebuild.args" ||
  fail "build did not default to the Debug configuration"
grep -q "com.zerodelta.gradus.ios$" "$STATE/xcrun.calls" ||
  fail "launched something other than the iOS app's bundle id"
grep -q "generate" "$STATE/xcodegen.calls" ||
  fail "did not regenerate the project from project.yml before building"
pass "builds, verifies, installs and launches"

# --- CODE_SIGN_ENTITLEMENTS must not be forced on the command line ------------
# xcodebuild applies a command-line build setting to *every* target, so an
# override meant for the app also lands on the embedded widget, which normally
# carries only its app group. The entitlement is pinned in the file instead.
if grep -q "CODE_SIGN_ENTITLEMENTS" "$STATE/xcodebuild.args"; then
  fail "build forced CODE_SIGN_ENTITLEMENTS, which also rewrites the widget's"
fi
pass "does not override entitlements on the command line"

# --- A codesign that cannot read the bundle is named as such ------------------
reset_state
write_state codesign_fails "1"
if output="$(run_install --device TESTDEVICE 2>&1)"; then
  fail "installed a bundle whose entitlements could never be read"
fi
grep -q "codesign" <<<"$output" ||
  fail "a codesign failure was reported as an entitlements problem: $output"
[[ ! -s "$STATE/xcrun.calls" ]] || fail "talked to the device after a failed read"
pass "a codesign failure is named, not reported as a wrong environment"

# --- Output plus a nonzero exit is still a failure ----------------------------
# The guard reads codesign's status explicitly rather than piping it straight
# into plutil: a pipeline whose status is discarded would read this bundle's
# plausible-looking Production plist and install it.
reset_state
write_state codesign_fails_after_output "1"
if run_install --device TESTDEVICE >/dev/null 2>&1; then
  fail "accepted entitlements from a codesign that exited nonzero"
fi
[[ ! -s "$STATE/xcrun.calls" ]] ||
  fail "installed on the strength of output from a failed codesign"
pass "refuses entitlements from a codesign that printed a plist then failed"

# --- --dry-run verifies without touching the device ---------------------------
reset_state
run_install --device TESTDEVICE --dry-run >/dev/null 2>&1 || fail "dry run failed"
[[ ! -s "$STATE/xcrun.calls" ]] ||
  fail "dry run talked to the device: $(cat "$STATE/xcrun.calls")"
# Without these two, --dry-run would still pass if it skipped the build and the
# verification entirely and just exited 0.
grep -q -- "-derivedDataPath" "$STATE/xcodebuild.args" || fail "--dry-run never built"
pass "--dry-run builds and verifies without installing"

reset_state
write_state signed_env "Development"
if run_install --device TESTDEVICE --dry-run >/dev/null 2>&1; then
  fail "--dry-run passed a Development-container build"
fi
pass "--dry-run still rejects the wrong container"

# --- --no-launch installs without launching -----------------------------------
reset_state
run_install --device TESTDEVICE --no-launch >/dev/null 2>&1 || fail "no-launch run failed"
grep -q "device install app" "$STATE/xcrun.calls" || fail "--no-launch did not install"
if grep -q "device process launch" "$STATE/xcrun.calls"; then
  fail "--no-launch launched anyway"
fi
pass "--no-launch installs without launching"

# --- A failed build never reaches the verify or install steps -----------------
reset_state
write_state xcodebuild_fails "1"
# Pre-create the bundle the build would have produced. Without this the case is
# not discriminating: the fake build leaves no bundle, so `[[ -d "$APP" ]]`
# catches it and the build guard itself is never exercised.
mkdir -p "$TEST_ROOT/dd/Build/Products/Debug-iphoneos/GradusiOS.app/PlugIns/GradusWidget.appex"
if run_install --device TESTDEVICE >/dev/null 2>&1; then
  fail "a failed build was reported as success"
fi
[[ ! -s "$STATE/xcrun.calls" ]] || fail "installed after a failed build"
pass "a failed build stops the run even when a stale bundle is on disk"

# --- A missing bundle is caught even when the build claims success ------------
reset_state
write_state skip_bundle "1"
if output="$(run_install --device TESTDEVICE 2>&1)"; then
  fail "proceeded with no app bundle on disk"
fi
grep -q "no app bundle" <<<"$output" || fail "unclear error for a missing bundle: $output"
pass "a missing app bundle stops the run"

# --- A failed install is reported, and nothing is launched --------------------
reset_state
write_state install_fails "1"
if output="$(run_install --device TESTDEVICE 2>&1)"; then
  fail "a failed install was reported as success"
fi
if grep -q "device process launch" "$STATE/xcrun.calls"; then
  fail "launched after a failed install"
fi
grep -q "ddiServicesAvailable" <<<"$output" ||
  fail "install failure did not point at the real diagnostic: $output"
pass "a failed install stops before launch and names the diagnostic"

# --- A multi-valued entitlement is refused, and named for what it is ----------
# `plutil -extract ... raw` prints an array's element *count*, so without a type
# check this refusal reads "environment is '2'".
reset_state
write_state signed_env "MULTI"
if output="$(run_install --device TESTDEVICE 2>&1)"; then
  fail "installed a bundle with a multi-valued CloudKit environment"
fi
grep -q "multi-valued" <<<"$output" ||
  fail "an array-valued entitlement was not named as such: $output"
[[ ! -s "$STATE/xcrun.calls" ]] || fail "installed despite a multi-valued entitlement"
pass "refuses a multi-valued environment and names the shape"

# --- The widget must not inherit the app's entitlements -----------------------
# This is the leak the whole script was written around; verifying only the app
# would leave it undetected.
reset_state
write_state widget_env "Production"
if output="$(run_install --device TESTDEVICE 2>&1)"; then
  fail "installed with the app's entitlements leaked into the widget"
fi
grep -q "leaked" <<<"$output" || fail "widget leak was not named: $output"
[[ ! -s "$STATE/xcrun.calls" ]] || fail "installed despite a widget entitlement leak"
pass "refuses when the app's entitlements have leaked into the widget"

reset_state
output="$(run_install --device TESTDEVICE 2>&1)" || fail "clean run failed: $output"
grep -q "widget carries no CloudKit environment" <<<"$output" ||
  fail "a clean run never checked the widget: $output"
pass "a clean run checks the widget explicitly"

# --- A failed regenerate stops before the build -------------------------------
reset_state
write_state xcodegen_fails "1"
if run_install --device TESTDEVICE >/dev/null 2>&1; then
  fail "built on a project that could not be regenerated"
fi
[[ ! -s "$STATE/xcodebuild.args" ]] || fail "built after a failed xcodegen"
pass "a failed regenerate stops before the build"

# --- Argument handling --------------------------------------------------------
reset_state
if run_install >/dev/null 2>&1; then
  fail "ran with no device selected"
fi
pass "requires a device"

# `[[ $# -ge 2 ]]` alone accepts this, silently setting DEVICE=--dry-run and
# discarding the dry run -- so the device would have been touched for real.
reset_state
if output="$(run_install --device --dry-run 2>&1)"; then
  fail "accepted a flag as a device identifier"
fi
grep -q -- "--dry-run" <<<"$output" ||
  fail "did not name the flag it was handed as a device: $output"
[[ ! -s "$STATE/xcrun.calls" ]] || fail "touched the device after a malformed --device"
pass "--device rejects a flag as its argument"

reset_state
if run_install --device TESTDEVICE --nonsense >/dev/null 2>&1; then
  fail "accepted an unknown argument"
fi
pass "rejects unknown arguments"

reset_state
run_install --list >/dev/null 2>&1 || fail "--list failed"
grep -q "list devices" "$STATE/xcrun.calls" || fail "--list did not list devices"
pass "--list lists devices"

echo "PASS: install-ios-local.sh behavior tests"
