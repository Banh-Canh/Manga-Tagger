import atexit
import json
import logging
import subprocess
import sys
import os

from logging.handlers import RotatingFileHandler, SocketHandler
from pathlib import Path
from tkinter import filedialog, messagebox, Tk

import numpy
import psutil
from pythonjsonlogger import jsonlogger

from MangaTaggerLib.database import Database
from MangaTaggerLib.task_queue import QueueWorker
from MangaTaggerLib.api import AniList


class AppSettings:
    mode_settings = None
    timezone = None
    version = None

    image_dir = None
    library_dir = None
    is_network_path = None

    processed_series = None

    _log = None

    @classmethod
    def load(cls):
        settings_location = Path(Path.cwd(), 'settings.json')
        if Path(settings_location).exists():
            with open(settings_location, 'r') as settings_json:
                settings = json.load(settings_json)
        else:
            with open(settings_location, 'w+') as settings_json:
                settings = cls._create_settings()
                json.dump(settings, settings_json, indent=4)

        cls._initialize_logger(settings['logger'])
        cls._log = logging.getLogger(f'{cls.__module__}.{cls.__name__}')

        # Database Configuration
        cls._log.debug('Now setting database configuration...')

        Database.database_name = settings['database']['database_name']
        Database.host_address = settings['database']['host_address']
        Database.port = settings['database']['port']
        Database.username = settings['database']['username']
        Database.password = settings['database']['password']
        Database.auth_source = settings['database']['auth_source']
        Database.server_selection_timeout_ms = settings['database']['server_selection_timeout_ms']

        if os.getenv("MANGA_TAGGER_DB_NAME") is not None:
            Database.database_name = os.getenv("MANGA_TAGGER_DB_NAME")
        if os.getenv("MANGA_TAGGER_DB_HOST_ADDRESS") is not None:
            Database.host_address = os.getenv("MANGA_TAGGER_DB_HOST_ADDRESS")
        if os.getenv("MANGA_TAGGER_DB_PORT") is not None:
            Database.port = int(os.getenv("MANGA_TAGGER_DB_PORT"))
        if os.getenv("MANGA_TAGGER_DB_USERNAME") is not None:
            Database.username = os.getenv("MANGA_TAGGER_DB_USERNAME")
        if os.getenv("MANGA_TAGGER_DB_PASSWORD") is not None:
            Database.password = os.getenv("MANGA_TAGGER_DB_PASSWORD")
        if os.getenv("MANGA_TAGGER_DB_AUTH_SOURCE") is not None:
            Database.auth_source = os.getenv("MANGA_TAGGER_DB_AUTH_SOURCE")
        if os.getenv("MANGA_TAGGER_DB_SELECTION_TIMEOUT") is not None:
            Database.server_selection_timeout_ms = int(os.getenv("MANGA_TAGGER_DB_SELECTION_TIMEOUT"))

        cls._log.debug('Database settings configured!')
        Database.initialize()
        Database.print_debug_settings()

        # Download Directory Configuration
         # Set the download directory
        download_dir = Path(settings['application']['library']['download_dir'])
        if not download_dir.is_absolute():
             cls._log.warning(f'"{download_dir}" is not a valid path. The download directory must be an '
                                 f'absolute path, such as "/manga". Please select a new download path.')
             sys.exit("INVALID PATH, SET A VALID PATH !")
        if not Path(download_dir).exists():
             cls._log.info(f'Library directory "{AppSettings.library_dir}" does not exist; creating now.')
             Path(download_dir).mkdir()
        QueueWorker.download_dir = download_dir
        cls._log.info(f'Download directory has been set as "{QueueWorker.download_dir}"')

        # Set Application Timezone
        cls.timezone = settings['application']['timezone']
        if os.getenv('TZ') is not None:
            cls.timezone = os.getenv("TZ")
        cls._log.debug(f'Timezone: {cls.timezone}')

        # Dry Run Mode Configuration
        # No logging here due to being handled at the INFO level in MangaTaggerLib
        if settings['application']['dry_run']['enabled'] and os.getenv("MANGA_TAGGER_DRY_RUN") is None or os.getenv("MANGA_TAGGER_DRY_RUN") is not None and os.getenv("MANGA_TAGGER_DRY_RUN").lower() == 'true':
            cls.mode_settings = {'database_insert': settings['application']['dry_run']['database_insert'],
                                 'rename_file': settings['application']['dry_run']['rename_file'],
                                 'write_comicinfo': settings['application']['dry_run']['write_comicinfo']}

            if os.getenv("MANGA_TAGGER_DB_INSERT") is not None and os.getenv("MANGA_TAGGER_DB_INSERT").lower() == 'true':
                cls.mode_settings['database_insert'] = True
            elif os.getenv("MANGA_TAGGER_DB_INSERT") is not None:
                cls.mode_settings['database_insert'] = False

            if os.getenv("MANGA_TAGGER_RENAME_FILE") is not None and os.getenv("MANGA_TAGGER_RENAME_FILE").lower() == 'true':
                cls.mode_settings['rename_file'] = True
            elif os.getenv("MANGA_TAGGER_RENAME_FILE") is not None:
                cls.mode_settings['rename_file'] = False

            if os.getenv("MANGA_TAGGER_WRITE_COMICINFO") is not None and os.getenv("MANGA_TAGGER_WRITE_COMICINFO").lower() == 'true':
                cls.mode_settings['write_comicinfo'] = True
            elif os.getenv("MANGA_TAGGER_WRITE_COMICINFO") is not None:
                cls.mode_settings['write_comicinfo'] = False

        # Multithreading Configuration
        if settings['application']['multithreading']['threads'] <= 0 and int(os.getenv("MANGA_TAGGER_THREADS")) is None or int(os.getenv("MANGA_TAGGER_THREADS")) is not None and int(os.getenv("MANGA_TAGGER_THREADS")) <= 0:
            QueueWorker.threads = 1
        else:
            QueueWorker.threads = settings['application']['multithreading']['threads']
            if int(os.getenv("MANGA_TAGGER_THREADS")) is not None:
                QueueWorker.threads = int(os.getenv("MANGA_TAGGER_THREADS"))

        cls._log.debug(f'Threads: {QueueWorker.threads}')

        if settings['application']['multithreading']['max_queue_size'] < 0 and int(os.getenv("MANGA_TAGGER_MAX_QUEUE_SIZE")) is None or int(os.getenv("MANGA_TAGGER_MAX_QUEUE_SIZE")) is not None and int(os.getenv("MANGA_TAGGER_MAX_QUEUE_SIZE")) < 0:
            QueueWorker.max_queue_size = 0
        else:
            QueueWorker.max_queue_size = settings['application']['multithreading']['max_queue_size']
            if int(os.getenv("MANGA_TAGGER_MAX_QUEUE_SIZE")) is not None:
                QueueWorker.max_queue_size = int(os.getenv("MANGA_TAGGER_MAX_QUEUE_SIZE"))

        cls._log.debug(f'Max Queue Size: {QueueWorker.max_queue_size}')

        # Debug Mode - Prevent application from processing files
        if settings['application']['debug_mode'] and os.getenv("MANGA_TAGGER_DEBUG_MODE") is None or os.getenv("MANGA_TAGGER_DEBUG_MODE") is not None and os.getenv("MANGA_TAGGER_DEBUG_MODE").lower() == 'true':
            QueueWorker._debug_mode = True

        cls._log.debug(f'Debug Mode: {QueueWorker._debug_mode}')

        # Image Directory
        if settings['application']['image_dir'] is not None:
            cls.image_dir = settings['application']['image_dir']
            if not Path(cls.image_dir).exists():
                cls._log.info(f'Image directory "{cls.image_dir}" does not exist; creating now.')
                Path(cls.image_dir).mkdir()
            cls._log.debug(f'Image Directory: {cls.image_dir}')
        else:
            cls._log.debug(f'Image Directory not configured')

        # Manga Library Configuration
        if settings['application']['library']['dir'] is not None:
            cls.library_dir = settings['application']['library']['dir']
            cls._log.debug(f'Library Directory: {cls.library_dir}')

            cls.is_network_path = settings['application']['library']['is_network_path']

            if not Path(cls.library_dir).exists():
                cls._log.info(f'Library directory "{AppSettings.library_dir}" does not exist; creating now.')
                Path(cls.library_dir).mkdir()
        else:
            cls._log.critical('Manga Tagger cannot function without a library directory for moving processed '
                              'files into. Configure one in the "settings.json" and try again.')
            sys.exit(1)

        # Load necessary database tables
        Database.load_database_tables()

        # Initialize QueueWorker and load task queue
        QueueWorker.initialize()
        QueueWorker.load_task_queue()

        # Scan download directory for downloads not already in database upon loading
        cls._scan_download_dir()

        # Initialize API
        AniList.initialize()

        # Register function to be run prior to application termination
        atexit.register(cls._exit_handler)
        cls._log.debug(f'{cls.__name__} class has been initialized')

    @classmethod
    def _initialize_logger(cls, settings):
        logger = logging.getLogger('MangaTaggerLib')
        logging_level = settings['logging_level']
        log_dir = settings['log_dir']

        if logging_level.lower() == 'info' and os.getenv("MANGA_TAGGER_LOGGING_LEVEL") is None or os.getenv("MANGA_TAGGER_LOGGING_LEVEL").lower() == 'info':
            logging_level = logging.INFO
        elif logging_level.lower() == 'debug' and os.getenv("MANGA_TAGGER_LOGGING_LEVEL") is None or os.getenv("MANGA_TAGGER_LOGGING_LEVEL").lower() == 'debug':
            logging_level = logging.DEBUG
        else:
            logger.critical('Logging level not of expected values "info" or "debug". Double check the configuration'
                            'in settings.json and try again.')
            sys.exit(1)

        logger.setLevel(logging_level)

        # Create log directory and allow the application access to it
        if not Path(log_dir).exists():
            Path(log_dir).mkdir()

        # Console Logging
        if settings['console']['enabled'] and os.getenv("MANGA_TAGGER_LOGGING_CONSOLE") is None or os.getenv("MANGA_TAGGER_LOGGING_CONSOLE").lower() == 'true':
            log_handler = logging.StreamHandler()
            log_handler.setFormatter(logging.Formatter(settings['console']['log_format']))
            logger.addHandler(log_handler)

        # File Logging
        if settings['file']['enabled'] and os.getenv("MANGA_TAGGER_LOGGING_FILE") is None or os.getenv("MANGA_TAGGER_LOGGING_FILE").lower() == 'true':
            log_handler = cls._create_rotating_file_handler(log_dir, 'log', settings, 'utf-8')
            log_handler.setFormatter(logging.Formatter(settings['file']['log_format']))
            logger.addHandler(log_handler)

        # JSON Logging
        if settings['json']['enabled'] and os.getenv("MANGA_TAGGER_LOGGING_JSON") is None or os.getenv("MANGA_TAGGER_LOGGING_JSON").lower() == 'true':
            log_handler = cls._create_rotating_file_handler(log_dir, 'json', settings)
            log_handler.setFormatter(jsonlogger.JsonFormatter(settings['json']['log_format']))
            logger.addHandler(log_handler)

        # Check TCP and JSON TCP for port conflicts before creating the handlers
        if settings['tcp']['enabled']:
            tcp_logging = True
        else:
            tcp_logging = False
        if settings['json_tcp']['enabled']:
            json_tcp_logging = True
        else:
            json_tcp_logging = False

        if os.getenv("MANGA_TAGGER_LOGGING_TCP") is not None and os.getenv("MANGA_TAGGER_LOGGING_TCP").lower() == 'true':
            tcp_logging = True
        elif os.getenv("MANGA_TAGGER_LOGGING_TCP") is not None:
            tcp_logging = False
        if os.getenv("MANGA_TAGGER_LOGGING_JSONTCP") is not None and os.getenv("MANGA_TAGGER_LOGGING_JSONTCP").lower() == 'true':
            json_tcp_logging = True
        elif os.getenv("MANGA_TAGGER_LOGGING_JSONTCP") is not None:
            json_tcp_logging = False

        if tcp_logging and json_tcp_logging:
            if settings['tcp']['port'] == settings['json_tcp']['port']:
                logger.critical('TCP and JSON TCP logging are both enabled, but their port numbers are the same. '
                                'Either change the port value or disable one of the handlers in settings.json '
                                'and try again.')
                sys.exit(1)

        # TCP Logging
        if tcp_logging:
            log_handler = SocketHandler(settings['tcp']['host'], settings['tcp']['port'])
            log_handler.setFormatter(logging.Formatter(settings['tcp']['log_format']))
            logger.addHandler(log_handler)

        # JSON TCP Logging
        if json_tcp_logging:
            log_handler = SocketHandler(settings['json_tcp']['host'], settings['json_tcp']['port'])
            log_handler.setFormatter(jsonlogger.JsonFormatter(settings['json_tcp']['log_format']))
            logger.addHandler(log_handler)

    @staticmethod
    def _create_rotating_file_handler(log_dir, extension, settings, encoder=None):
        return RotatingFileHandler(Path(log_dir, f'MangaTagger.{extension}'),
                                   maxBytes=settings['max_size'],
                                   backupCount=settings['backup_count'],
                                   encoding=encoder)

    @classmethod
    def _exit_handler(cls):
        cls._log.info('Initiating shutdown procedures...')

        # Stop worker threads
        QueueWorker.exit()

        # Save necessary database tables
        Database.save_database_tables()

        # Close MongoDB connection
        Database.close_connection()

        cls._log.info('Now exiting Manga Tagger')

    @classmethod
    def _create_settings(cls):

        return {
            "application": {
                "debug_mode": False,
                "timezone": "America/New_York",
                "image_dir": "/manga",
                "library": {
                    "dir": "/manga/library",
                    "is_network_path": False,
                    "download_dir": "/manga"
                },
                "dry_run": {
                    "enabled": False,
                    "rename_file": False,
                    "database_insert": False,
                    "write_comicinfo": False
                },
                "multithreading": {
                    "threads": 8,
                    "max_queue_size": 0
                }
            },
            "database": {
                "database_name": "manga_tagger",
                "host_address": "localhost",
                "port": 27017,
                "username": "manga_tagger",
                "password": "Manga4LYFE",
                "auth_source": "admin",
                "server_selection_timeout_ms": 1
            },
            "logger": {
                "logging_level": "info",
                "log_dir": "logs",
                "max_size": 10485760,
                "backup_count": 5,
                "console": {
                    "enabled": True,
                    "log_format": "%(asctime)s | %(threadName)s %(thread)d | %(name)s | %(levelname)s - %(message)s"
                },
                "file": {
                    "enabled": True,
                    "log_format": "%(asctime)s | %(threadName)s %(thread)d | %(name)s | %(levelname)s - %(message)s"
                },
                "json": {
                    "enabled": True,
                    "log_format": "%(threadName)s %(thread)d %(asctime)s %(name)s %(levelname)s %(message)s"
                },
                "tcp": {
                    "enabled": False,
                    "host": "localhost",
                    "port": 1798,
                    "log_format": "%(threadName)s %(thread)d | %(asctime)s | %(name)s | %(levelname)s - %(message)s"
                },
                "json_tcp": {
                    "enabled": False,
                    "host": "localhost",
                    "port": 1798,
                    "log_format": "%(threadName)s %(thread)d %(asctime)s %(name)s %(levelname)s %(message)s"
                }
            }
        }

    @classmethod
    def _scan_download_dir(cls):
        for directory in QueueWorker.download_dir.iterdir():
            for manga_chapter in directory.glob('*.cbz'):
                if manga_chapter.name.strip('.cbz') not in QueueWorker.task_list.keys():
                    QueueWorker.add_to_task_queue(manga_chapter)


def compare(s1, s2):
    s1 = s1.lower().strip('/[^a-zA-Z ]/g", ')
    s2 = s2.lower().strip('/[^a-zA-Z ]/g", ')

    rows = len(s1) + 1
    cols = len(s2) + 1
    distance = numpy.zeros((rows, cols), int)

    for i in range(1, rows):
        distance[i][0] = i

    for i in range(1, cols):
        distance[0][i] = i

    for col in range(1, cols):
        for row in range(1, rows):
            if s1[row - 1] == s2[col - 1]:
                cost = 0
            else:
                cost = 2

            distance[row][col] = min(distance[row - 1][col] + 1,
                                     distance[row][col - 1] + 1,
                                     distance[row - 1][col - 1] + cost)

    return ((len(s1) + len(s2)) - distance[row][col]) / (len(s1) + len(s2))
