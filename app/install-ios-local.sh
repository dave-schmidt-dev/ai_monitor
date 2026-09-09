#!/usr/bin/env bash
# Builds Gradus for a physical iOS device, installs it, and launches it.
#
# This is the iOS counterpart to install-mac-local.sh, and the local-install
# alternative to archive-upload-ios.sh: no App Store Connect, no TestFlight, no
# Apple-side mutation beyond whatever automatic signing does to keep the
# development profile current.
#
# ## The check that earns this script its existence
#
# `com.apple.developer.icloud-container-environment` decides which CloudKit
# container the app reads. Xcode takes it from the provisioning profile unless
# the entitlements pin it, so a Debug build signed with a *development* profile
# reads the Development container while the Mac publishes to Production.
# Nothing errors. The dashboard sits on "Waiting for First Publish" while the
# Mac logs successful publishes every two minutes, which looks exactly like a
# broken CloudKit read and is not one -- that cost a session on 2026-09-08.
#
# project.yml now pins it for the GradusiOS target, so this script's job is to
# prove the pin survived into the signed binary rather than to reapply it. Note
# that project.yml is the source and GradusiOS.entitlements is XcodeGen output:
# a hand edit to the generated file is reverted by the next `xcodegen generate`.
# The script refuses to install a bundle whose *signed* entitlements do not say
# Production. A
# CODE_SIGN_ENTITLEMENTS override on the command line is deliberately not used:
# xcodebuild applies a command-line build setting to every target in the build,
# so overriding the app's entitlements also hands them to the embedded widget,
# which normally carries only its app group.
#
# ## Device selection
#
# `devicectl` identifiers are CoreDevice UUIDs, not the hardware UDIDs that
# appear in a provisioning profile, so there is no useful pre-flight membership
# check -- the install is the check. Devices connected over localNetwork can
# refuse the developer-disk-image mount with kAMDMobileImageMounterDeviceLocked
# while plainly unlocked; `ddiServicesAvailable: false` is the real signature
# and a cable is the usual fix. The error text is reported verbatim rather than
# interpreted.
#
# Usage:
#   ./install-ios-local.sh --list                     show connected devices
#   ./install-ios-local.sh --device <identifier>      build, install, launch
#   ./install-ios-local.sh --device X --dry-run       build and verify only
#   ./install-ios-local.sh --device X --no-launch     install without launching
#
# --device takes the CoreDevice identifier from --list, not a device name: the
# build passes it through as `-destination id=...`, which accepts identifiers
# only and fails in xcodebuild rather than at install time.
#
# Environment:
#   GRADUS_IOS_DERIVED_DATA  build directory (default $TMPDIR/gradus-ios-device)
#   GRADUS_IOS_CONFIGURATION xcodebuild configuration (default Debug)
#   XCODEBUILD               path to xcodebuild
#   XCRUN                    path to xcrun
#   CODESIGN                 path to codesign
#   XCODEGEN                 path to xcodegen
set -euo pipefail

unset HISTFILE
set +o history 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$SCRIPT_DIR/Gradus.xcodeproj"
SCHEME="GradusiOS"
BUNDLE_ID="com.zerodelta.gradus.ios"
REQUIRED_CLOUDKIT_ENVIRONMENT="Production"
REQUIRED_APNS_ENVIRONMENT="development"
# plutil reads `.` as a key-path separator, so the entitlement name has to be
# escaped or the extraction silently resolves nothing and the guard rejects
# every build, correct one included.
CLOUDKIT_ENVIRONMENT_KEYPATH='com\.apple\.developer\.icloud-container-environment'
APNS_ENVIRONMENT_KEYPATH='aps-environment'

XCODEBUILD="${XCODEBUILD:-xcodebuild}"
XCRUN="${XCRUN:-xcrun}"
CODESIGN="${CODESIGN:-codesign}"
XCODEGEN="${XCODEGEN:-xcodegen}"
CONFIGURATION="${GRADUS_IOS_CONFIGURATION:-Debug}"
DERIVED_DATA="${GRADUS_IOS_DERIVED_DATA:-${TMPDIR:-/tmp}}"
DERIVED_DATA="${GRADUS_IOS_DERIVED_DATA:-${DERIVED_DATA%/}/gradus-ios-device}"

DEVICE=""
DRY_RUN=0
LAUNCH=1
LIST_ONLY=0

die() {
  echo "FAIL: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)
      # `[[ $# -ge 2 ]]` alone accepts `--device --dry-run`, which silently
      # becomes DEVICE="--dry-run" with DRY_RUN still 0 -- a typo that discards
      # the flag it looks like it set and then fails naming a destination.
      [[ $# -ge 2 && "$2" != -* ]] ||
        die "--device needs a CoreDevice identifier (see --list), got '${2:-<nothing>}'"
      DEVICE="$2"
      shift 2
      ;;
    --list)
      LIST_ONLY=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-launch)
      LAUNCH=0
      shift
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

if [[ "$LIST_ONLY" -eq 1 ]]; then
  "$XCRUN" devicectl list devices
  exit 0
fi

[[ -n "$DEVICE" ]] || die "no device selected; run with --list for connected device identifiers"

APP="$DERIVED_DATA/Build/Products/$CONFIGURATION-iphoneos/$SCHEME.app"

# Every sibling build script regenerates first (install-mac-local.sh,
# notarize-mac.sh, archive-upload-ios.sh). Skipping it here would let a tree
# whose project.yml is ahead of the generated files build stale -- which is
# precisely how the CloudKit pin went missing in the first place.
echo "==> Regenerating Xcode project from project.yml"
"$XCODEGEN" generate --project "$SCRIPT_DIR" --spec "$SCRIPT_DIR/project.yml" ||
  die "xcodegen generate failed"

echo "==> Building $SCHEME ($CONFIGURATION) for $DEVICE"
# Automatic signing needs an Apple ID in Xcode's accounts; without one this
# fails with "No Accounts" rather than anything about entitlements. The two
# provisioning flags let it register an unseen device and refresh a development
# profile whose capabilities have drifted from the entitlements file.
if ! "$XCODEBUILD" \
  -project "$PROJECT" \
  -scheme "$SCHEME" \
  -configuration "$CONFIGURATION" \
  -destination "id=$DEVICE,platform=iOS" \
  -derivedDataPath "$DERIVED_DATA" \
  -allowProvisioningUpdates \
  -allowProvisioningDeviceRegistration \
  build; then
  die "build failed"
fi

[[ -d "$APP" ]] || die "no app bundle at $APP"

# The signed binary is the only artifact that matters here: reading the
# entitlements *file* would just re-assert what the repo already says, and the
# whole failure mode is a value that is correct in the source and wrong in the
# product.
assert_cloudkit_environment() {
  local target="$1" label="$2" entitlements actual

  # Read and extract in two steps. Piping them and swallowing the status with
  # `|| true` -- which a bare assignment needs, or `set -e` kills the script
  # before `die` can name anything -- reports a codesign that could not read the
  # bundle at all as "environment is <unset>". That is the same misdiagnosis
  # this script exists to prevent, one layer down: a tooling failure wearing an
  # entitlements failure's error message.
  if ! entitlements="$("$CODESIGN" -d --entitlements :- "$target" 2>/dev/null)"; then
    die "$label could not be read by codesign at $target (a signing or tooling failure, not an entitlements one)"
  fi

  # `plutil -extract ... raw` prints an *array's element count*, not its
  # contents, so a multi-valued entitlement -- the shape a profile grant uses --
  # would be refused as "environment is '2'". Check the type first and name the
  # real shape.
  local shape
  shape="$(/usr/bin/plutil -extract "$CLOUDKIT_ENVIRONMENT_KEYPATH" xml1 -o - - \
    <<<"$entitlements" 2>/dev/null || true)"
  if grep -q "<array>" <<<"$shape"; then
    die "$label CloudKit environment is multi-valued, expected the single string $REQUIRED_CLOUDKIT_ENVIRONMENT"
  fi

  actual="$(/usr/bin/plutil -extract "$CLOUDKIT_ENVIRONMENT_KEYPATH" raw -o - - \
    <<<"$entitlements" 2>/dev/null || true)"
  if [[ "$actual" != "$REQUIRED_CLOUDKIT_ENVIRONMENT" ]]; then
    die "$label CloudKit environment is '${actual:-<unset>}', expected $REQUIRED_CLOUDKIT_ENVIRONMENT (it would read the wrong container and show \"Waiting for First Publish\" forever)"
  fi
  echo "    $label CloudKit environment: $actual"
}

# APNs ignores namespaced lookalikes of its canonical entitlement key.
# Verify the canonical entitlement on the signed product so a source typo or
# provisioning mismatch cannot produce an installed app that never registers.
assert_apns_environment() {
  local target="$1" label="$2" entitlements actual

  if ! entitlements="$("$CODESIGN" -d --entitlements :- "$target" 2>/dev/null)"; then
    die "$label could not be read by codesign at $target (a signing or tooling failure, not an entitlements one)"
  fi
  actual="$(/usr/bin/plutil -extract "$APNS_ENVIRONMENT_KEYPATH" raw -o - - \
    <<<"$entitlements" 2>/dev/null || true)"
  if [[ "$actual" != "$REQUIRED_APNS_ENVIRONMENT" ]]; then
    die "$label APNs environment is '${actual:-<unset>}', expected $REQUIRED_APNS_ENVIRONMENT"
  fi
  echo "    $label APNs environment: $actual"
}

# The widget has no CloudKit entitlement, and the leak that motivated this
# script gave it the app's. Assert the absence directly rather than trusting
# that nothing reintroduced a project-level override.
refute_cloudkit_environment() {
  local target="$1" label="$2" entitlements
  [[ -d "$target" ]] || return 0
  if ! entitlements="$("$CODESIGN" -d --entitlements :- "$target" 2>/dev/null)"; then
    die "$label could not be read by codesign at $target (a signing or tooling failure)"
  fi
  if /usr/bin/plutil -extract "$CLOUDKIT_ENVIRONMENT_KEYPATH" raw -o - - \
    <<<"$entitlements" >/dev/null 2>&1; then
    die "$label carries a CloudKit environment entitlement it has no use for; the app's entitlements have leaked into it"
  fi
  echo "    $label carries no CloudKit environment: OK"
}

echo "==> Verifying signed entitlements"
assert_cloudkit_environment "$APP" "app"
assert_apns_environment "$APP" "app"
refute_cloudkit_environment "$APP/PlugIns/GradusWidget.appex" "widget"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "==> Dry run: built and verified $APP, installing nothing"
  exit 0
fi

echo "==> Installing to $DEVICE"
if ! "$XCRUN" devicectl device install app --device "$DEVICE" "$APP"; then
  die "install failed (a device connected over localNetwork can report the developer disk image as locked while unlocked; check ddiServicesAvailable and try a cable)"
fi

if [[ "$LAUNCH" -eq 0 ]]; then
  echo "==> Installed; not launching"
  exit 0
fi

echo "==> Launching $BUNDLE_ID"
if ! "$XCRUN" devicectl device process launch --device "$DEVICE" "$BUNDLE_ID"; then
  die "launch failed"
fi

echo "==> Installed and launched $BUNDLE_ID on $DEVICE"
