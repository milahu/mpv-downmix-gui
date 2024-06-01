from setuptools import setup

#with open('requirements.txt') as f:
#    install_requires = f.read().splitlines()

setup(
  name='mpv-downmix-gui',
  version='0.0.3',
  #install_requires=install_requires,
  install_requires=[
    'tkinter',
  ],
  scripts=[
    #'mpv_downmix_gui.py',
  ],
  entry_points={
    'gui_scripts': [
      'mpv-downmix-gui = mpv_downmix_gui.mpv_downmix_gui:main',
    ],
  },
)
