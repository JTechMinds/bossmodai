/**
 * Nest git credential Add/Edit — read masked token, validate SSH, field errors.
 */
const BossModNestGitCredentialForm = (() => {
    const EMPTY_MSG = (
        'Paste a GitHub access token or an SSH key. An empty field doesn’t save.'
    );
    const SSH_INVALID_MSG = (
        'Paste a full SSH private key (starts with -----BEGIN).'
    );
    const SSH_HEADER = /-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----/;

    function sshLooksValid(text) {
        const key = String(text || '').trim();
        if (!key) return true;
        return SSH_HEADER.test(key) && /-----END/.test(key);
    }

    function read(scope) {
        const patInput = scope && scope.querySelector('[data-credential-field="pat"]');
        const sshInput = scope && scope.querySelector('[data-credential-field="ssh"]');
        const pat = typeof BossModSecretField !== 'undefined'
            ? BossModSecretField.read(patInput)
            : String((patInput && patInput.value) || '').trim();
        const rawSsh = String((sshInput && sshInput.value) || '');
        const ssh = rawSsh.trim() ? rawSsh : '';
        return { pat, ssh, patInput, sshInput };
    }

    function validate(scope, options) {
        const requireSecret = !(options && options.requireSecret === false);
        const { pat, ssh } = read(scope);
        const errors = {};
        if (requireSecret && !pat && !ssh) {
            errors.pat = EMPTY_MSG;
        }
        if (ssh && !sshLooksValid(ssh)) {
            errors.ssh = SSH_INVALID_MSG;
        }
        const ok = Object.keys(errors).length === 0;
        return { pat, ssh, errors, ok };
    }

    function clearErrors(scope) {
        if (!scope) return;
        scope.querySelectorAll('[data-credential-field-error]').forEach((node) => node.remove());
        scope.querySelectorAll('[data-credential-field]').forEach((input) => {
            input.classList.remove('border-red-400');
        });
    }

    function errorAnchor(scope, field) {
        const input = scope && scope.querySelector(`[data-credential-field="${field}"]`);
        if (!input) return null;
        if (field === 'pat') {
            const row = input.closest('.flex');
            if (row) return row;
        }
        return input;
    }

    function showErrors(scope, errors) {
        clearErrors(scope);
        if (!scope || !errors) return;
        Object.keys(errors).forEach((field) => {
            const message = errors[field];
            if (!message) return;
            const anchor = errorAnchor(scope, field);
            const input = scope.querySelector(`[data-credential-field="${field}"]`);
            if (input) input.classList.add('border-red-400');
            if (!anchor) return;
            const note = document.createElement('p');
            note.className = 'text-xs text-red-600 mt-1';
            note.dataset.credentialFieldError = field;
            note.textContent = message;
            if (typeof anchor.insertAdjacentElement === 'function') {
                anchor.insertAdjacentElement('afterend', note);
            } else {
                anchor.parentNode.insertBefore(note, anchor.nextSibling);
            }
        });
    }

    /**
     * @returns {{ pat?: string, ssh_key?: string, blocked: boolean }}
     */
    function payloadForSave(scope, options) {
        const checked = validate(scope, options);
        const { pat, ssh, errors } = checked;
        const sshInvalid = Boolean(errors.ssh);
        const nothingToSave = Boolean(errors.pat) && !pat;
        if (nothingToSave || (sshInvalid && !pat)) {
            showErrors(scope, errors);
            return { blocked: true };
        }
        const out = {};
        if (pat) out.pat = pat;
        if (ssh && !sshInvalid) out.ssh_key = ssh;
        if (sshInvalid) {
            showErrors(scope, { ssh: errors.ssh });
        } else {
            clearErrors(scope);
        }
        return Object.assign(out, { blocked: false });
    }

    return {
        EMPTY_MSG,
        SSH_INVALID_MSG,
        read,
        validate,
        clearErrors,
        showErrors,
        payloadForSave,
        sshLooksValid,
    };
})();
