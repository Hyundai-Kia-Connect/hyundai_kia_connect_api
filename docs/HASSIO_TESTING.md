## Testing Kia/Hyundai API PRs in Home Assistant (Hass.io)

These steps show how to validate this API-only pull request inside a Home Assistant installation. No custom `kia_uvo` code is required—only the Python package needs to be overridden and the manifest must force Home Assistant to install it.

### 1. Open a shell inside the Home Assistant container

```bash
docker exec -it homeassistant bash
# replace `homeassistant` if your container name differs
```

### 2. Clear cached dependencies

```bash
rm -rf /config/deps
pip uninstall -y hyundai_kia_connect_api || true
```

### 3. Install the PR branch of `hyundai_kia_connect_api`

```bash
pip install --force-reinstall \
  "git+https://github.com/renatkh/hyundai_kia_connect_api.git@fix/eu-ca-refresh-token"
python3 -m pip show hyundai_kia_connect_api  # should report version 99.99.99
```

### 4. Point the Home Assistant integration at the same branch

Edit `/config/custom_components/kia_uvo/manifest.json` (create the folder if it does not exist yet) and ensure the requirements list contains the branch pin:

```json
"requirements": [
  "hyundai_kia_connect_api @ git+https://github.com/renatkh/hyundai_kia_connect_api.git@fix/eu-ca-refresh-token"
]
```

This is the only integration change required for this PR.

### 5. Restart Home Assistant

Either use **Settings → System → Restart** or run:

```bash
ha core restart
```

### 6. Confirm the override after restart

From the container shell:

```bash
python3 -m pip show hyundai_kia_connect_api
```

Verify that the reported version is `99.99.99` and the location points to the site-packages directory inside the container.

### 7. Validate the flow

1. Configure or reload the Kia/Hyundai integration.
2. Complete the OTP flow (email/SMS) when prompted.
3. Ensure entities load and commands (e.g., the update button) succeed.
4. Leave the integration running for at least an hour to confirm the token refresh continues to work with the new API build.

Please report whether the test passes and which region/brand you exercised so we can ensure non-US regions remain healthy.

