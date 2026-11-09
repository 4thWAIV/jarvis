"""Include the existing package factory's canonical assets in installed wheels."""
from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).parent
FACTORY_FILES = [ROOT / 'docs' / name for name in ('making-a-package.md', 'work-packages.md')]
FACTORY_FILES += [ROOT / 'examples/packages/zip_package.py']
FACTORY_FILES += [path for path in (ROOT / 'examples/packages/decision-deck').rglob('*')
                  if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc']


class BuildPy(build_py):
    def run(self):
        super().run()
        for source in FACTORY_FILES:
            target = Path(self.build_lib) / 'jarvis' / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.copy_file(str(source), str(target))


setup(cmdclass={'build_py': BuildPy})
