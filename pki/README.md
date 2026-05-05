# macOS Certificate Authority for Caddy

Manage mutual TLS client certificates using LibreSSL on macOS for use with Caddy. The CA configuration template ([`ca.cnf.template`](./ca.cnf.template)) is stored separately in this repository.

---

## Table of Contents

- [Create the CA](#create-the-ca)
- [Issue Client Certificates](#issue-client-certificates)
- [Install the Client Certificate](#install-the-client-certificate)

---

## Create the CA

1. Create the CA directory structure:

   ```bash
   mkdir -p ~/myca/{certs,private}
   chmod 700 ~/myca/private
   cd ~/myca
   ```

2. Create the CA index:

   ```bash
   touch ~/myca/index.txt
   chmod 600 ~/myca/index.txt
   echo 1000 > ~/myca/serial
   chmod 600 ~/myca/serial
   ```

3. Copy [`ca.cnf`](./ca.cnf.template) from this repository into `~/myca/`:

   ```bash
   cp /path/to/repo/pki/ca.cnf.template ~/myca/ca.cnf
   ```

   > **Note:** Edit `ca.cnf` and replace the `dir` value under `[CA_default]` with your actual home directory path before proceeding.

4. Generate the CA private key:

   ```bash
   openssl genrsa -aes256 -out ~/myca/private/ca.key 4096
   ```

5. Set permissions on the private key:

   ```bash
   chmod 400 ~/myca/private/ca.key
   ```

6. Self-sign the CA certificate:

   ```bash
   openssl req -new -x509 -days 365 -config ~/myca/ca.cnf -extensions v3_ca \
     -key ~/myca/private/ca.key -out ~/myca/certs/ca.crt
   ```

---

## Issue Client Certificates

> Repeat these steps for each client, substituting `client-1` with an appropriate client name.

1. Generate the client private key:

   ```bash
   openssl ecparam -genkey -name prime256v1 -out ~/myca/private/client-1.key
   ```

2. Create the certificate signing request (CSR):

   ```bash
   openssl req -new -key ~/myca/private/client-1.key \
     -out ~/myca/client-1.csr -subj "/CN=Client 1"
   ```

3. Sign the client certificate:

   ```bash
   openssl ca -config ~/myca/ca.cnf -extensions v3_client \
     -extfile ~/myca/ca.cnf -in ~/myca/client-1.csr \
     -out ~/myca/certs/client-1.crt
   ```

4. Clean up the CSR:

   ```bash
   rm ~/myca/client-1.csr
   ```

5. Bundle the certificate as a `.p12` for distribution:

   **Linux / standard OpenSSL:**
   ```bash
   openssl pkcs12 -export \
     -in ~/myca/certs/client-1.crt \
     -inkey ~/myca/private/client-1.key \
     -certfile ~/myca/certs/ca.crt \
     -out ~/myca/certs/client-1.p12 \
     -name "Client 1"
   ```

   **macOS (requires `-legacy` flag):**
   ```bash
   openssl pkcs12 -export -legacy \
     -in ~/myca/certs/client-1.crt \
     -inkey ~/myca/private/client-1.key \
     -certfile ~/myca/certs/ca.crt \
     -out ~/myca/certs/client-1.p12 \
     -name "Client 1"
   ```

6. Verify the certificate extensions:

   ```bash
   openssl x509 -in ~/myca/certs/client-1.crt -text -noout \
     | grep -A5 "Key Usage\|Extended"
   ```

7. Verify the certificate chain of trust:

   ```bash
   openssl verify -CAfile ~/myca/certs/ca.crt ~/myca/certs/client-1.crt
   ```

---

## Install the Client Certificate

### macOS

1. Trust the CA system-wide:

   ```bash
   security add-trusted-cert -d -r trustRoot \
     -k /Library/Keychains/System.keychain ~/myca/certs/ca.crt
   ```

2. Install the client certificate into the login keychain:

   ```bash
   security import ~/myca/certs/client-macos.p12 \
     -k ~/Library/Keychains/login.keychain-db
   ```
