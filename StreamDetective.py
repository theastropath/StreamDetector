import sys

if sys.version_info[0] < 3:
	raise ImportError('Python < 3 is unsupported.')
if sys.version_info[0] == 3 and sys.version_info[1] < 6:
	raise ImportError('Python < 3.6 is unsupported.')

from urllib.parse import urlencode
from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError, InvalidURL, ConnectionError
import requests
import json
import os.path
import sys
import tempfile
import traceback
from hashlib import sha1
from datetime import datetime
import time

clientId=""
accessToken=""

path = os.path.realpath(os.path.dirname(__file__))
tempDir = os.path.join(tempfile.gettempdir(),"streams")

configFileName="config.json"

class StreamDetective:
    def __init__ (self):
        self.session=Session()
        retryAdapter = HTTPAdapter(max_retries=2)
        self.session.mount('https://',retryAdapter)
        self.session.mount('http://',retryAdapter)

        self.streamsUrl='https://api.twitch.tv/helix/streams?game_id='
        self.gameIdUrlBase='https://api.twitch.tv/helix/games?name='

        if self.HandleConfigFile():
            print("Created default config.json file")
            exit(0)
        self.HandleGames()
        
    def HandleConfigFile(self):
        configFileFullPath = os.path.join(path,configFileName)
        print("Doing this")
        if os.path.exists(configFileFullPath):
            with open(configFileFullPath, 'r') as f:
                self.config = json.load(f)

        else:
            print("Writing default config.json file")
            config = {}
            exampleConfigFileFullPath = os.path.join(path,"config.example.json")
            with open(exampleConfigFileFullPath, 'r') as f:
                config = json.load(f)
            
            with open(configFileFullPath, 'w') as f:
                json.dump(config,f, indent=4)

            return True

    def HandleGames(self):
        for game in self.config["Games"]:
            try:
                self.HandleGame(game)
            except Exception as e:
                logex(e, 'error in', game)


    def TwitchApiRequest(self, url, headers):
        response = None
        try:
            response = self.session.get( url, headers=headers)
            result = json.loads(response.text)
        except Exception as e:
            print('request for '+url+' failed: ', e)
            raise

        if not result:
            if response and response.headers:
                print(repr(response.headers))
            print('request for '+url+' failed')
            raise Exception('request failed')
        if 'status' in result and result['status'] != 200:
            if response and response.headers:
                print(repr(response.headers))
            print('request for '+url+' failed with status:', result['status'], ', result: ', result)
            raise Exception('request for '+url+' failed with status:', result['status'], ', result: ', result)
        return result

    def GetGameId(self, game):
        gameIdUrl = self.gameIdUrlBase+game["GameName"]
        gameId = 0

        headers = {
                'Client-ID': self.config["clientId"],
                'Authorization': 'Bearer '+self.config["accessToken"],
                'Content-Type': 'application/json'
                  }

        # TODO: cache gameIds so we can continue if this fails, or even skip this API call
        result = self.TwitchApiRequest(gameIdUrl,headers)

        if "data" in result and len(result["data"])==1:
            gameId = result["data"][0]["id"]
        else:
            raise Exception(gameIdUrl+" response expected 1 game id: ", result)

        if not gameId:
            raise Exception('gameId is missing')
            
        return gameId

    def GetAllStreams(self,game,gameId):
        print("GetAllStreams")
        streamsUrl = self.streamsUrl+gameId
        
        allStreams = []
        keepGoing = True
        cursor = ""
        while keepGoing:
            headers = {
                    'Client-ID': self.config["clientId"],
                    'Authorization': 'Bearer '+self.config["accessToken"],
                    'Content-Type': 'application/json'
                      }
                      
            url = streamsUrl+"&first=100" #Fetch 100 streams at a time
			
            if cursor!="":
                url+="&after="+cursor
			
            result = None
            result = self.TwitchApiRequest(url,headers)
            if "pagination" in result and "cursor" in result["pagination"]:
                keepGoing = True
                cursor = result["pagination"]["cursor"]
            else:
                keepGoing = False
                cursor = ""
                
                
            for stream in result['data']:
                allStreams.append(stream)
                print(stream["user_login"])
				
            if keepGoing:
                time.sleep(0.25) #pace yourself a little bit
            
        return allStreams
        
        

    def CheckStream(self, filter, streamer, title, tags):
        #print("-------")
        #print("Name: "+streamer)
        #print(title)

        if filter.get("MatchTag","")=="" and filter.get("MatchString","")=="":
            return True

        if "MatchTag" in filter and filter["MatchTag"]!="":
            if filter["MatchTag"] in tags:
                return True
        if "MatchString" in filter and filter["MatchString"]!="":
            if filter["MatchString"].lower() in title.lower():
                return True
        return False

    def ReadGameCache(self, game):
        saveLocation = os.path.join(tempDir,game["GameName"])
        if os.path.exists(saveLocation):
            try:
                f = open(saveLocation,'r')
                streamInfoOld = json.load(f)
                f.close()
                return streamInfoOld
            except Exception as e:
                logex(e, 'ReadGameCache failed at:', saveLocation, ', with config:', game)
        return None

    def HandleGame(self,game):
        print("Handling "+game["GameName"])
        
        gameId = self.GetGameId(game)

        streamInfo = self.ReadGameCache(game)
        hadCache = True
        if streamInfo is None:
            streamInfo = {}
            hadCache = False
        newStreams = []
        
        allStreams = self.GetAllStreams(game,gameId)

        now = datetime.now()

        for stream in allStreams:
            id = stream['id']
            streamer = stream['user_login']
            title = stream['title']
            tags = stream['tag_ids']
            stream['last_seen'] = now.isoformat()
            matched = self.CheckStream(game, streamer, title, tags)
            if matched:
                print("matched "+streamer)
                stream['last_matched'] = now.isoformat()
                if id not in streamInfo:
                    newStreams.append(stream)
            streamInfo[id] = stream
                
        print("-------")
        
        # All stream info now retrieved
        if hadCache:
            print("New Streams: "+str(newStreams))
            self.genWebhookMsgs(game["DiscordWebhook"], game["GameName"], newStreams)
            for stream in newStreams:
                id = stream['id']
                streamInfo[id] = stream
        else:
            newStreams = []
            print("Old streams cache not found, creating it now")
            
        # cleanup old entries in cache
        for key, val in streamInfo.items():
            last_seen = fromisoformat(val['last_seen'])
            if (now - last_seen).total_seconds() > (3600*24):
                del streamInfo[key]

        if not os.path.exists(tempDir):
            os.makedirs(tempDir)

        saveLocation = os.path.join(tempDir,game["GameName"])
        f = open(saveLocation,'w')
        json.dump(streamInfo,f)
        f.close()


    def sendWebhookMsg(self, webhookUrl, gameName, toSend):
        content = ''
        embeds = []
        for stream in toSend:
            url="https://twitch.tv/"+stream["user_login"]
            content += url + ' is playing ' + gameName + '\n'
            streamer = stream["user_name"]
            title = stream["title"]
            embeds.push({"title":streamer,"url":url,"description":title})
        
        data={
            "username":self.config["DiscordWebhookUser"],
            "content": content,
            "embeds": embeds
        }
        response = requests.post(webhookUrl,json=data)
        print("Webhook Response: "+str(response.status_code)+" contents: "+str(response.content))

    def genWebhookMsgs(self, webhookUrl, gameName, newList):
        if not webhookUrl:
            return
        IgnoreStreams = self.config.get('IgnoreStreams', [])
        toSend = []
        for stream in newList:
            if stream["user_login"] in IgnoreStreams:
                continue
            last_notified = fromisoformat(stream.get('last_notified'))
            now = datetime.now()
            # update this timestamp so we don't just notify again later?
            # this whole thing might be obsolete since we use the id of the stream instead of the streamer username?
            # cooldown should probably be per streamer not stream id
            stream['last_notified'] = now.isoformat()
            if (now - last_notified).total_seconds() < self.config['CooldownSeconds']:
                continue
            toSend.append(stream)
        
        if toSend:
            self.sendWebhookMsg(webhookUrl, gameName, toSend)

def logex(e, *args):
    estr = "".join(traceback.format_exception(BaseException, e, e.__traceback__))
    print("\nERROR: "+estr, *args, '\n')

def fromisoformat(iso):
    # for compatibility with python 3.6
    if not iso:
        return datetime(1970, 1, 1)
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f")

sd = StreamDetective()
