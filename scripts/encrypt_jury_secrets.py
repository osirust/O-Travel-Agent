"""Prepare the organisers' binary age envelope from a separate jury-only file.

Default status never reads .env values. No Master lookup, key argument, provider
call, Git mutation, decryption or automatic upload exists in this helper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

RECIPIENT = "age1932fu7f70rwyfhaxflyfszz4gf6avxwc95wc0yjwm6zns9tspycqpgdnc0"
RECIPIENT_SHA256 = "4c665c3e34c86188bfbad780f9b1c36c4de5e37be7e3b35a2cbca179bce6b1f8"
OFFICIAL_SOURCE = "https://docs.ostrovok.tech/s/b9e6ec5e-5ad6-4091-9c85-a48eee9b21cc/doc/shifrovanie-sekretov-klyuchej-6Qhc4gJOPZ"
AGE = Path("/home/aegml/tools/age-1.3.2/age")
AGE_SHA256 = "eb7dd1b518f0a307c99cd97782623c5321da049154b04acd2d98d21aa7bc9b2c"
JURY_ENV = Path("/home/aegml/projects/otravel/.local/submission/jury.env")
ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[A-Z][A-Z0-9_]{0,79}")


class SubmissionError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise SubmissionError(code)


def parse_env(raw: bytes, *, empty=False) -> dict[str, str]:
    require(isinstance(raw, bytes) and 0 < len(raw) <= 131072, "ENV_SIZE_INVALID")
    require(not raw.startswith(b"\xef\xbb\xbf"), "ENV_UTF8_BOM_FORBIDDEN")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise SubmissionError("ENV_UTF8_REQUIRED") from None
    require("\x00" not in text, "ENV_NUL_FORBIDDEN")
    values = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(bool(separator) and NAME.fullmatch(key) is not None, "ENV_NAME_OR_ASSIGNMENT_INVALID")
        require(key not in values, "ENV_DUPLICATE_VARIABLE")
        require(value == value.strip(), "ENV_VALUE_EDGE_WHITESPACE_FORBIDDEN")
        require('"' not in value and "'" not in value, "ENV_QUOTES_FORBIDDEN")
        require(not empty or value == "", "EXAMPLE_VALUES_MUST_BE_EMPTY")
        values[key] = value
    require(bool(values) and len(values) <= 256, "ENV_VARIABLE_COUNT_INVALID")
    return values


def read_jury_env(path=JURY_ENV) -> bytes:
    path = Path(path).absolute()
    require(path.name == "jury.env" and path == path.resolve() and not path.is_symlink(), "JURY_ONLY_PATH_REQUIRED")
    require(path.is_file(), "JURY_ENV_NOT_FOUND")
    require(os.name == "posix" and path.stat().st_mode & 0o077 == 0, "JURY_ENV_PRIVATE_0600_REQUIRED")
    raw = path.read_bytes()
    parse_env(raw)
    return raw


def verified_age(path=AGE) -> Path:
    path = Path(path).absolute()
    require(path == path.resolve() and path.is_file() and not path.is_symlink(), "AGE_BINARY_NOT_FOUND")
    require(hashlib.sha256(path.read_bytes()).hexdigest() == AGE_SHA256, "AGE_BINARY_SHA_MISMATCH")
    return path


def encrypt_bytes(raw: bytes, recipient: str, output: Path, *, replace=False) -> dict:
    """Low-level synthetic-testable operation. CLI always pins official recipient."""
    parse_env(raw)
    age = verified_age()
    require(isinstance(recipient, str) and re.fullmatch(r"age1[0-9a-z]{58}", recipient) is not None,
        "AGE_RECIPIENT_INVALID")
    output = Path(output).absolute()
    require(output.parent == output.parent.resolve() and not output.is_symlink(), "OUTPUT_PATH_NOT_CANONICAL")
    require(not output.exists() or replace, "ENCRYPTED_OUTPUT_ALREADY_EXISTS")
    temporary = output.with_name("." + output.name + "." + uuid4().hex + ".tmp")
    try:
        result = subprocess.run([str(age), "--encrypt", "-r", recipient, "-o", str(temporary)],
            input=raw, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}, timeout=30, check=False)
        require(result.returncode == 0, "AGE_ENCRYPT_FAILED")
        cipher = temporary.read_bytes()
        require(cipher.startswith(b"age-encryption.org/v1\n") and not cipher.startswith(b"-----BEGIN")
            and len(cipher) > len(raw) and raw not in cipher, "AGE_BINARY_OUTPUT_INVALID")
        if replace:
            os.replace(temporary, output)
        else:
            # link is an atomic no-overwrite operation; a concurrent output is
            # never silently replaced. Source and destination share a directory.
            os.link(temporary, output)
            temporary.unlink()
        return {"ciphertext_sha256": hashlib.sha256(cipher).hexdigest(), "ciphertext_bytes": len(cipher)}
    except subprocess.TimeoutExpired:
        raise SubmissionError("AGE_ENCRYPT_TIMEOUT") from None
    finally:
        if temporary.exists():
            temporary.unlink()


HOW = {
    "GROQ_API_KEY": "Отдельный ключ для жюри: https://console.groq.com/keys; Free без billing.",
    "ZAI_API_KEY": "Отдельный ключ для жюри: https://z.ai/manage-apikey/apikey-list; только разрешённая бесплатная модель.",
    "OPENROUTER_API_KEY": "Отдельный ключ для жюри: https://openrouter.ai/workspaces/default/keys; только :free.",
    "GIGACHAT_AUTH_KEY": "Отдельный ключ для жюри в Personal API-проекте GigaChat; не основной Authorization Key.",
    "GEMINI_API_KEY": "Отдельный ключ для жюри в Google AI Studio; использовать только подтверждённую бесплатную квоту.",
    "CLOUDFLARE_API_TOKEN": "Отдельный Workers AI token нужного аккаунта Cloudflare, без billing.",
    "LITEAPI_API_KEY": "Sandbox API key в кабинете Nuitee/LiteAPI; production не требуется.",
    "DUFFEL_ACCESS_TOKEN": "Test access token в кабинете Duffel; live booking не включать.",
    "SERPAPI_API_KEY": "Private API key в кабинете SerpApi; только бесплатный план.",
}


def empty_example(names) -> bytes:
    lines = ["# Значения здесь пустые. Рабочие значения передаются только в secrets.env.age.",
             "# Defaults и параметры запуска описаны в README и docker-compose.yml."]
    for name in names:
        require(NAME.fullmatch(name) is not None, "EXAMPLE_VARIABLE_NAME_INVALID")
        comment = HOW.get(name)
        if comment is None:
            comment = ("Необязательный ключ выбранного поставщика; брать только в его официальном кабинете."
                if any(part in name for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                else "Параметр приложения; значение и default описаны в README/Compose.")
        lines.extend(["# " + comment, name + "="])
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    parse_env(raw, empty=True)
    return raw


def selected_jury_llm(values):
    fields = {"groq": ("GROQ_API_KEY", "GROQ_MODEL", "GROQ_FREE_TIER_CONFIRMED"),
        "zai": ("ZAI_API_KEY", "ZAI_MODEL", "ZAI_FREE_TIER_CONFIRMED"),
        "gigachat": ("GIGACHAT_AUTH_KEY", "GIGACHAT_MODEL", "GIGACHAT_FREEMIUM_CONFIRMED"),
        "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FREE_TIER_CONFIRMED"),
        "cloudflare": ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL", "CLOUDFLARE_FREE_TIER_CONFIRMED")}
    provider = values.get("LLM_PROVIDER", "")
    if provider == "openrouter":
        require(bool(values.get("OPENROUTER_API_KEY")) and
            values.get("OPENROUTER_MODEL", "").endswith(":free"), "JURY_FREE_LLM_CONFIGURATION_REQUIRED")
        require(not values.get("OPENROUTER_BACKUP_MODEL") or values["OPENROUTER_BACKUP_MODEL"].endswith(":free"),
            "JURY_FREE_LLM_CONFIGURATION_REQUIRED")
    else:
        require(provider in fields, "JURY_SELECTED_LLM_PROVIDER_REQUIRED")
        key, model, confirmed = fields[provider]
        require(bool(values.get(key)) and bool(values.get(model)) and
            values.get(confirmed, "").casefold() in {"true", "1", "yes"}, "JURY_FREE_LLM_CONFIGURATION_REQUIRED")
    return provider


def external_jury_llm(values):
    """Travel-only envelope: the recipient supplies every LLM credential.

    Explicit opt-in never turns an incomplete LLM profile into tested inference.
    The default encryption path still requires a dedicated selected LLM key.
    """
    prefixes = ('LLM_', 'OPENAI_', 'ANTHROPIC_', 'GEMINI_', 'GROQ_', 'OPENROUTER_',
                'CLOUDFLARE_', 'GIGACHAT_', 'ZAI_', 'NVIDIA_', 'COHERE_')
    credentials = [name for name in values if name.startswith(prefixes)
                   and name.endswith(('_KEY', '_TOKEN', '_SECRET'))]
    require(all(values[name] == '' for name in credentials), 'EXTERNAL_LLM_ENVELOPE_MUST_NOT_CONTAIN_LLM_KEYS')
    require(bool(values.get('LITEAPI_API_KEY')) or bool(values.get('DUFFEL_ACCESS_TOKEN'))
            or bool(values.get('SERPAPI_API_KEY')), 'TRAVEL_ONLY_ENVELOPE_REQUIRES_TRAVEL_KEY')
    return {'llm_key_provided': False, 'recipient_llm_configuration_required': True,
            'free_dialogue_ready_without_recipient_key': False}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name == "nt":
        script = str(Path(__file__).resolve())
        script = "/mnt/" + script[0].lower() + script[2:].replace("\\", "/")
        # Only flags/file paths cross argv. No value collection or environment
        # credential forwarding is implemented here.
        completed = subprocess.run(["wsl.exe", "-d", "AegML-Hackathon", "-u", "root", "--",
            "python3", "-B", script, *argv], check=False)
        return completed.returncode
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--check", action="store_true")
    action.add_argument("--encrypt", action="store_true")
    action.add_argument("--prepare-empty-example", action="store_true")
    parser.add_argument("--confirm-jury-only-keys", action="store_true")
    parser.add_argument("--confirm-project-travel-keys", action="store_true",
                        help="Owner-authorized sharing of existing project travel test/search keys; no LLM key included.")
    parser.add_argument("--external-llm-key", action="store_true",
                        help="Encrypt only travel credentials; recipient must configure their own LLM API.")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(hashlib.sha256(RECIPIENT.encode("ascii")).hexdigest() == RECIPIENT_SHA256,
            "OFFICIAL_RECIPIENT_PIN_MISMATCH")
        if args.prepare_empty_example:
            public = parse_env((ROOT / ".env.example").read_bytes())
            target = ROOT / ".local" / "submission" / ".env.example"
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(empty_example(public))
            print(json.dumps({"status": "EMPTY_EXAMPLE_CANDIDATE_READY", "path": str(target), "variables": len(public)}))
            return 0
        if not args.check and not args.encrypt:
            print(json.dumps({"status": "STATUS_ONLY_NO_ENV_READ", "jury_env_exists": JURY_ENV.is_file(),
                "jury_env_path": str(JURY_ENV), "recipient": RECIPIENT, "recipient_sha256": RECIPIENT_SHA256,
                "age_binary_exists": AGE.is_file(), "output": str(ROOT / "secrets.env.age")}))
            return 0
        if args.encrypt:
            if args.external_llm_key:
                require(args.confirm_project_travel_keys, "PROJECT_TRAVEL_KEY_SHARING_CONFIRMATION_REQUIRED")
            else:
                require(args.confirm_jury_only_keys, "JURY_ONLY_KEY_PROVENANCE_CONFIRMATION_REQUIRED")
        raw = read_jury_env()
        values = parse_env(raw)
        example = parse_env((ROOT / ".env.example").read_bytes(), empty=True)
        require(set(values) == set(example), "JURY_ENV_EXAMPLE_NAMES_MUST_MATCH")
        llm_delivery = external_jury_llm(values) if args.external_llm_key else {
            'llm_key_provided': True, 'provider': selected_jury_llm(values)}
        if args.external_llm_key:
            llm_delivery['key_provenance'] = 'OWNER_PROVIDED_PROJECT_TRAVEL_KEYS_NOT_REISSUED_LLM_KEYS'
        if not args.encrypt:
            print(json.dumps({"status": "JURY_FORMAT_VALID", "variables": len(values), "values_printed": False,
                              **llm_delivery}))
            return 0
        result = encrypt_bytes(raw, RECIPIENT, ROOT / "secrets.env.age", replace=args.replace)
        print(json.dumps({"status": "JURY_ENV_ENCRYPTED_BINARY", "output": str(ROOT / "secrets.env.age"),
            "recipient_sha256": RECIPIENT_SHA256, "variables": len(values), **llm_delivery, **result}))
        return 0
    except (SubmissionError, OSError, UnicodeError) as error:
        code = str(error) if isinstance(error, SubmissionError) else type(error).__name__
        print(json.dumps({"status": "NOT_ENCRYPTED", "code": code, "values_printed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
