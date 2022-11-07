import typing

from telethon import TelegramClient, events, sync
import os
import sqlite3
import wand.image
import json
import time
import queue
import re
import shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pyffmpeg import FFmpeg
import PIL.Image

def checkValidFile(filePath):
    base = os.path.basename(filePath)
    if base[0] == '.':
        print("Skip hidden file:" + filePath)
        return False
    elif base == '@eaDir' or '@eaDir' in filePath:
        print("Skip Synology file:" + filePath)
        return False
    elif 'Syno' in filePath or 'SYNO' in filePath:
        print("Skip Synology file:" + filePath)
        return False
    return True


class WatchDogWorker(FileSystemEventHandler):
    def __init__(self, multiThreadQueue: queue):
        self.queue = multiThreadQueue

    # watch dog callback, on different thread
    def on_modified(self, event):
        if os.path.isfile(event.src_path):

            print("modified file:", event.src_path)
            if not checkValidFile(event.src_path):
                return
            print("Put into pending queue")
            self.queue.put(event.src_path)


class ScanWorker:
    def __init__(self, configDict: typing.Dict, multiThreadQueue: queue):
        self.queue = multiThreadQueue
        self.api_id = configDict['api_id']
        self.api_hash = configDict['api_hash']
        self.tg_channel = configDict['tg_channel']
        self.session_name = configDict['session_name']
        self.target_path = configDict['target_path']
        self.waitSecond = configDict['flood_wait_sec']
        self.force_send_file = configDict['force_send_file']
        self.temp_folder = './tmp'
        self.db_name = "./config/" + os.path.basename(self.target_path) + ".db"

        # start tg client
        self.tg_client = TelegramClient("./config/" + self.session_name, self.api_id, self.api_hash)
        self.tg_client.start()
        # avoid easy flood wait
        self.channelEntity = self.tg_client.get_entity(self.tg_channel)

    def cleanUp(self):
        if self.tg_client is not None:
            self.tg_client.disconnect()

    # Printing upload progress
    def progressCallback(self, current, total):
        print('Uploaded', current, 'out of', total, 'bytes: {:.2%}'.format(current / total))

    def sendFile(self, fileFullPath):
        filename = os.path.basename(fileFullPath)
        try:
            name, file_extension = os.path.splitext(filename)
            if file_extension is not None and file_extension == '.HEIC':
                newFilePath = os.path.join(self.temp_folder, name + '.jpg')
                img = wand.image.Image(filename=fileFullPath)
                img.format = 'jpg'
                img.save(filename=newFilePath)
                img.close()
                res = self.uploadFileTG(newFilePath, self.force_send_file)
                os.remove(newFilePath)
                return res
            else:
                return self.uploadFileTG(fileFullPath, self.force_send_file)

        except Exception as ex:
            print(f'file handling Error on {fileFullPath}:' + str(ex))

        return False

    def uploadFileTG(self, fileFullPath, sendAsFile=False):
        try:
            print("send file:" + fileFullPath)
            filename = os.path.basename(fileFullPath)
            name, file_extension = os.path.splitext(filename)
            file_extension = file_extension.lower()

            size = os.path.getsize(fileFullPath)
            if size > 2147483648:
                print("File larger than 2GB!!")
                return False

            # 10MB max for image in telegram
            sendForFile = True if size > 10485760 or sendAsFile else False

            isVideo = False
            isImage = False

            if '.mov' == file_extension or '.mp4' == file_extension or \
                    '.mkv' == file_extension or '.avi' == file_extension or \
                    '.m4v' == file_extension or '.flv' == file_extension or \
                    '.wmv' == file_extension:
                isVideo = True

            # gif is well supported
            if '.jpg' == file_extension or '.jpeg' == file_extension or \
                    '.png' == file_extension or '.bmp' == file_extension or '.tiff' == file_extension :
                isImage = True


            if isVideo:
                inf = fileFullPath
                outf = os.path.join(self.temp_folder, name + '.jpg')

                ff = FFmpeg()
                ff.convert(inf, outf)

                # creating thumbnail
                image = PIL.Image.open(outf)
                maxsize = (320, 320)
                image.thumbnail(maxsize)
                thumbnail = outf
                image.save(thumbnail)
                image.close()
                print("send as file:" + str(sendForFile))
                self.tg_client.send_file(entity=self.channelEntity, file=fileFullPath, background=True, thumb=thumbnail,
                                        progress_callback=self.progressCallback, force_document=sendForFile)
                os.remove(thumbnail)

            elif isImage:

                if not sendForFile:
                    im = PIL.Image.open(fileFullPath)
                    w, h = im.size
                    if w > 2560 or h > 2560:
                        sendForFile = True
                    im.close()

                # creating thumbnail
                image = PIL.Image.open(fileFullPath)
                maxsize = (320, 320)
                image.thumbnail(maxsize)
                thumbnail = os.path.join(self.temp_folder, name + "_thumb.jpg")
                image.save(thumbnail)
                image.close()

                print("send as file:" + str(sendForFile))
                self.tg_client.send_file(entity=self.channelEntity, file=fileFullPath, background=True, thumb=thumbnail,
                                         progress_callback=self.progressCallback, force_document=sendForFile)
                os.remove(thumbnail)

            else:
                print("send as file:" + str(sendForFile))
                self.tg_client.send_file(entity=self.channelEntity, file=fileFullPath, background=True,
                                         progress_callback=self.progressCallback, force_document=sendForFile)

            print(f"send file completed sleep for {self.waitSecond} sec")
            time.sleep(self.waitSecond)

            return True

        except Exception as ex:
            errStr = str(ex)
            print("Error:" + errStr)
            if errStr.startswith("A wait of"):
                digit = re.findall(r'\d+', errStr)
                if len(digit) > 0:
                    print("Flood wait error happened...wait for " + str(digit[0]) + " seconds..")
                    time.sleep(int(digit[0]))
            elif "DIMENSIONS" in errStr.upper():
                print("Image exceed dimensions, Resend as file")
                self.uploadFileTG(fileFullPath, True)

        return False

    def insertDB(self, dbCur, dbCon, filename, lastModified, fileFullPath):
        try:
            # insert a record
            dbCur.execute(f"""
                            INSERT INTO FileStat VALUES
                                ('{filename}','{lastModified}', false,'{fileFullPath}')
                        """)
            dbCon.commit()
            return True
        except sqlite3.Error as ex:
            print(f'Sql Error on {fileFullPath}:' + str(ex))
        return False

    def updateDBonSuccess(self, dbCur, dbCon, filename, lastModified):
        try:
            # update a record
            dbCur.execute(f"""
                        UPDATE FileStat
                        SET is_uploaded = 1
                        WHERE file_name = '{filename}' AND modified_date = '{lastModified}'
                        """)
            dbCon.commit()
            return True
        except sqlite3.Error as ex:
            print(f'Sql Error on {filename}:' + str(ex))
        return False

    def scanFolder(self):
        print("scan folder:" + self.target_path)

        dbCon = sqlite3.connect(self.db_name)
        cur = dbCon.cursor()
        cur.execute("""
                        CREATE TABLE IF NOT EXISTS 'FileStat' (
                            'file_name'	TEXT NOT NULL,
                            'modified_date'	TEXT,
                            'is_uploaded'	INTEGER,
                            'full_path'	TEXT NOT NULL
                        ) 
                    """)
        dbCon.commit()

        if os.path.exists(self.temp_folder):
            shutil.rmtree(self.temp_folder)

        os.mkdir(self.temp_folder)
        # iterate over files in
        # that directory
        for root, dirs, files in os.walk(self.target_path):
            for filename in files:

                fileFullPath = os.path.join(root, filename)

                if not checkValidFile(fileFullPath):
                    continue

                lastModified = str(os.path.getmtime(fileFullPath))

                try:
                    # check exist
                    res = cur.execute(
                        f"SELECT * FROM FileStat WHERE file_name='{filename}' AND modified_date='{lastModified}'")
                    record = res.fetchone()

                    if record is None:
                        # insert a record
                        if not self.insertDB(cur, dbCon, filename, lastModified, fileFullPath):
                            return False
                    else:
                        if record[2]:  # uploaded boolean
                            print("Skip uploaded file:" + fileFullPath)
                            continue

                except sqlite3.Error as ex:
                    print(f'Sql Error on {fileFullPath}:' + str(ex))
                    return False

                if self.sendFile(fileFullPath):
                    if not self.updateDBonSuccess(cur, dbCon, filename, lastModified):
                        return False

        print("Done scan all files in:" + self.target_path)
        cur.close()
        dbCon.close()
        return True

    def workOnQueue(self):
        if not self.queue.empty():
            dbCon = sqlite3.connect(self.db_name)
            cur = dbCon.cursor()

            while not self.queue.empty():
                fileFullPath = self.queue.get()
                if not os.path.exists(fileFullPath):
                    print(f"Error: {fileFullPath} not exist anymore!")
                    continue
                filename = os.path.basename(fileFullPath)
                lastModified = str(os.path.getmtime(fileFullPath))
                if self.insertDB(cur, dbCon, filename, lastModified, fileFullPath):
                    if self.sendFile(fileFullPath):
                        self.updateDBonSuccess(cur, dbCon, filename, lastModified)

            cur.close()
            dbCon.close()


if __name__ == '__main__':

    configData = None
    try:
        with open('./config/config.json', 'r') as f:
            configData = json.load(f)
    except Exception as exp:
        print(f'IO Error on config.json:' + str(exp))

    if configData is None:
        print('Cannot find config.json!')
        exit(1)

    myObserver = Observer()
    scanWorkers = []
    for configDict in configData:
        insertQueue = queue.Queue()  # for insert row in multithread
        worker = ScanWorker(configDict, insertQueue)

        # initial scan
        if worker.scanFolder():
            if 'watchdog' in configDict and configDict['watchdog']:
                scanWorkers.append(worker)
                # create watchdog and insert new file to queue on event
                myObserver.schedule(WatchDogWorker(insertQueue), path=worker.target_path, recursive=True)
        else:
            print("Error when scaning folder...stop this worker")

    print("Done scanning all folders")
    if len(scanWorkers) > 0:
        print("Start watchdog")
        myObserver.start()
        while True:
            try:
                time.sleep(5)
                for worker in scanWorkers:
                    worker.workOnQueue()
            except Exception as ex:
                for worker in scanWorkers:
                    worker.cleanUp()
                myObserver.stop()
                print(str(ex))
                exit(1)

    print("End of script")
