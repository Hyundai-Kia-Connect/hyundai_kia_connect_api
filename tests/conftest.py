from dotenv import load_dotenv


load_dotenv()


def pytest_configure(config):
    config.addinivalue_line("markers", "br: mark test for the Brazilian API")
