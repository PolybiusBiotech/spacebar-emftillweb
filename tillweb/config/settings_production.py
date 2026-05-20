# Django settings for production use
# flake8: noqa

from .settings import *

if django_config.get("mode", "production") == "production":
    DEBUG = False
    TEMPLATE_DEBUG = False

    STATIC_ROOT = django_config["staticfiles_dir"]
    MEDIA_ROOT = django_config["mediafiles_dir"]

    ALLOWED_HOSTS = django_config["allowed_hosts"]

    STORAGES = {
        "default": {
            "BACKEND":
            "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND":
            "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
        },
    }

    # Ensure that the secret key has been set and we are not falling back
    # to the default
    with open(django_config["secret_key_file"]) as f:
        SECRET_KEY = f.read().strip()

    # Read the "line note" password
    with open(django_config["line_note_password_file"]) as f:
        LINE_NOTE_PASSWORD = f.read().strip()

    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        "formatters": {
            "verbose": {
                "format": "[{asctime}] {levelname}: {name}.{message}",
                "style": "{",
            },
        },

        'handlers': {
            "console": {'level': 'ERROR', "class": "logging.StreamHandler",
                        "formatter": "verbose"},
        },
        "root": {
            "handlers": ["console"],
            "level": "ERROR",
        },
    }
