#!/usr/bin/env python

"""The setup script."""

from setuptools import setup, find_packages

with open("README.rst") as readme_file:
    readme = readme_file.read()

with open("HISTORY.rst") as history_file:
    history = history_file.read()

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

long_description = readme + "\n\n" + history
long_description = readme

test_requirements = [
    "pytest>=3",
]

setup(
    author="Fuat Akgun",
    author_email="fuatakgun@gmail.com",
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3.10",
    ],
    description="Python Boilerplate contains all the boilerplate you need to create a Python package.",
    install_requires=requirements,
    license="MIT license",
    long_description=long_description,
    include_package_data=True,
    keywords="hyundai_kia_connect_api",
    name="hyundai_kia_connect_api",
    packages=find_packages(
        include=["hyundai_kia_connect_api", "hyundai_kia_connect_api.*"]
    ),
    entry_points={
        "console_scripts": ["bluelink = hyundai_kia_connect_api.bluelink:main"]
    },
    test_suite="tests",
    tests_require=test_requirements,
    url="https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api",
    version="3.43.4",
    zip_safe=False,
)
