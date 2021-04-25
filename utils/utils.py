from time import sleep
import subprocess, threading
import re
import sys
import os, glob
import logging

class LoggingLevel:
    LEVELS = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR,
        'critical': logging.CRITICAL
    }
    LEVELS_KEYS = list(LEVELS.keys())

    def __init__(self, level_str):
        self.level_str = level_str.lower()
        self.level = LoggingLevel.LEVELS.get(self.level_str)


def ellipsize(full_str, max_length, ellipsis="..."):
    max_length -= len(ellipsis)
    return full_str[:max_length] + (full_str[max_length:] and ellipsis)

def pargs_to_cmd(pargs):
    return re.sub(r"[\[\]\'\,]", '', str(pargs))

def youtube_link_to_id(link):
    m = re.match(r"^.*(youtu\.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*", link)
    video_id = None
    if m is not None:
        video_id_m = m.group(2)
        # youtube ids are always this length
        if len(video_id_m) == 11:
            video_id = video_id_m
    return video_id

def remove_dir_contents(dir):
    g = glob.glob(f"{dir}/*")
    g_hidden = glob.glob(f"{dir}/.*")
    g.extend(g_hidden)
    for f in g:
        os.remove(f)

class SubprocessThread(threading.Thread):
    class RunException(Exception):
        pass

    def __init__(self, pargs, logfile=None):
        threading.Thread.__init__(self)
        self._stop_event = threading.Event()
        self.pargs = pargs
        self.logfile = logfile
        self.returncode = -1

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def proc(self):
        popen = None
        f = None
        if self.logfile is None:
            popen = subprocess.Popen(self.pargs, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            f = open(self.logfile, "a")
            f.write(f"{str(self.pargs)}\n")
            f.flush()
            popen = subprocess.Popen(self.pargs, stdout=f, stderr=subprocess.STDOUT, universal_newlines=True)

        while True:
            if self.stopped():
                popen.terminate()
            if popen.poll() is not None: 
                if f is not None:
                    f.close() 
                self.returncode = popen.returncode
                logging.warning(f"Process '{ellipsize(pargs_to_cmd(self.pargs), 75)}' exited with code {self.returncode}")
                return
            sleep(1)

    def run(self):
        self.proc()
    
    def get_return_code(self):
        return self.returncode