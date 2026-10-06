# Ubuntu 24.04+ explicit userns permission; Chromium still applies its own sandbox.
profile chromix-docker flags=(unconfined) {
    userns,
}
