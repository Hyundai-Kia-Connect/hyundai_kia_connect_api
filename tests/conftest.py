from dotenv import load_dotenv
from pytest_socket import disable_socket


load_dotenv()


def pytest_configure(config):
    config.addinivalue_line("markers", "br: mark test for the Brazilian API")


def pytest_runtest_setup():
    disable_socket()
