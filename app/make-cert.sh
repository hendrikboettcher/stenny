#!/bin/zsh
# Creates "Stenny Dev", a self-signed code-signing certificate in your login keychain (once per Mac).
# build.sh signs Stenny.app with it, so every build has the same signature and macOS keeps the
# microphone, calendar and notification permissions across rebuilds. It stays on this Mac and is only
# used to sign Stenny; nothing is sent anywhere. (codesign uses it as it is: it doesn't need to be
# marked as trusted, so there's no password prompt.)
set -euo pipefail

NAME="${1:-Stenny Dev}"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -p codesigning | grep -q "\"$NAME\""; then
    echo "✅ Certificate \"$NAME\" already exists, nothing to do."
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
CNF

# macOS's own openssl (LibreSSL): its .p12 files are the kind `security import` accepts
OPENSSL=/usr/bin/openssl
"$OPENSSL" req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$TMP/cert.cnf" \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" 2>/dev/null
"$OPENSSL" pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -name "$NAME" \
    -out "$TMP/cert.p12" -passout pass:stenny

# -T: codesign may use the key without asking every time
security import "$TMP/cert.p12" -k "$KEYCHAIN" -P stenny -T /usr/bin/codesign >/dev/null
echo "✅ Certificate \"$NAME\" added to the login keychain."
