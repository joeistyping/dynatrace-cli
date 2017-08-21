# Required Libraries
import sys
import io
import re
import os
import json
import datetime
import operator
import urllib
import requests

# Constants
API_ENDPOINT_APPLICATIONS = "/api/v1/entity/applications"
API_ENDPOINT_SERVICES = "/api/v1/entity/services"
API_ENDPOINT_PROCESS_GROUPS = "/api/v1/entity/infrastructure/process-groups"
API_ENDPOINT_HOSTS = "/api/v1/entity/infrastructure/hosts"
API_ENDPOINT_TIMESERIES = "/api/v1/timeseries"
API_ENDPOINT_EVENTS = "/api/v1/events"
API_ENDPOINT_PROBLEMS = "/api/v1/problem"

# Configuration is read from config file if exists. If you want to go back to default simply delete the config file
dtconfigfilename = os.path.dirname(os.path.abspath(__file__)) + "\\" + "dtconfig.json"
config = {
    "tenanthost"  : "smpljson",   # "abc12345.live.dynatrace.com" # this would be the configuration for a specific Dynatrace SaaS Tenant
    "apitoken"    : "smpltoken",  # YOUR API TOKEN, generated with Dynatrace
    "cacheupdate" : -1            # -1 = NEVER, 0=ALWAYS, X=After X seconds
}

global_doPrint = False

def getAuthenticationHeader():
    return {"Authorization" : "Api-Token " + config["apitoken"]}

def getRequestUrl(apiEndpoint, queryString):
    requestUrl = "https://" + config["tenanthost"] + apiEndpoint;
    if(queryString is not None and len(queryString) > 0):
        requestUrl += "?" + queryString
    return requestUrl

def getCacheFilename(apiEndpoint, queryString):
    fullCacheFilename = os.path.dirname(os.path.abspath(__file__)) + "\\" + config["tenanthost"].replace(".", "_") + "\\" + apiEndpoint.replace("/","_")
    if(queryString is not None and len(queryString) > 0):
        fullCacheFilename += "\\" + urllib.parse.unquote(queryString).replace(".", "_").replace(":", "_").replace("?", "_").replace("&", "_");
    fullCacheFilename += ".json"

    return fullCacheFilename

class NameValue:
    def __init__(self, defaultName, defaultValue):
        self.name = defaultName

        # we allow values to be object lists - so we simply load it as JSON
        if(defaultValue.startswith("[") and defaultValue.endswith("]")):
            json.load(defaultValue)
        else:
            self.value = defaultValue

# helper functions
def parseNameValue(nameValue, defaultName, defaultValue):
    "Allowed strings are: justvalue, name=value, [value1,value2], name=[value1,value2]"

    # first we check for None and just return defaultName, defaultValue
    if(nameValue is None):
        return NameValue(defaultName, defaultValue)

    # now we check for name=value pair or just value
    equalSign = nameValue.find("=")
    if(equalSign < 0):
        return NameValue(defaultName, nameValue)

    partitions = nameValue.partition("=")
    return NameValue(partitions[0], partitions[2])

def queryDynatraceAPI(isGet, apiEndpoint, queryString, postBody):
    # we first validate if we have the file in cache. NOTE: we only store HTTP GET data in the Cache. NO POST!
    fullCacheFilename = getCacheFilename(apiEndpoint, queryString)
    readFromCache = False
    if(os.path.isfile(fullCacheFilename)):
        cacheupdate = config["cacheupdate"]
        if(cacheupdate == -1):
            readFromCache = True
        if(cacheupdate > 0):
            now = datetime.datetime.now()
            lastModified = datetime.datetime.fromtimestamp(os.path.getmtime(fullCacheFilename))
            if((now - lastModified).seconds < cacheupdate):
                readFromCache = True

    jsonContent = None
    if isGet and readFromCache:
        with open(fullCacheFilename) as json_data:
            jsonContent = json.load(json_data)
    else:
        myResponse = None
        if isGet:
            myResponse = requests.get(getRequestUrl(apiEndpoint, queryString), headers=getAuthenticationHeader(), verify=True)
        else:
            myResponse = requests.post(getRequestUrl(apiEndpoint, queryString), headers=getAuthenticationHeader(), verify=True, json=postBody)

        # For successful API call, response code will be 200 (OK)
        if(myResponse.ok):
            jsonContent = json.loads(myResponse.text)

            if isGet:
                # lets ensure the directory is there
                directory = os.path.dirname(fullCacheFilename)
                if not os.path.exists(directory):
                    os.makedirs(directory)

                # now lets save the content to the cache as well
                with open(fullCacheFilename, "w+") as output_file:
                    json.dump(jsonContent, output_file)

        else:
            jsonContent = json.loads(myResponse.text)
            errorMessage = ""
            if(jsonContent["error"]):
                errorMessage = jsonContent["error"]["message"]
                if global_doPrint:
                    print("Dynatrace API returned an error: " + errorMessage)
            jsonContent = None
            raise Exception("Error", "Dynatrace API returned an error: " + errorMessage)

    return jsonContent

class KeySearch:
    # key allows a regular keyname but also a format of [keylistname/][context:][key][?valuekey] - example: tags/AWS:Name
    # some examples of allowed key names
    # - displayName=.*easyTravel                   -> match on value displayName=.*easyTravel
    # - tags/AWS:Name=.*host.*                     -> match parent list=tags, context=AWS,key=Name and value=.*host.*
    # - tags/Name=.*host.*                         -> match parent list=tags, key=Name and value=.*host.*
    # - tags/context#AWS:key#Name=value#.*host.*   -> match parent list=tags, context=AWS,key=Name and value=.*host.*
    def __init__(self, key):
        self.keylistname = None         # name of list in case we match on a list, e.g: tags
        self.contextvalue = None        # value of the context, e.g: AWS
        self.contextkeyname = None      # name of the context key fiels, e.g: context
        self.keyvalue = None            # value of the key field, e.g: Name
        self.keykeyname = None          # name of the key key field, e.g: key
        self.value = None               # value of the value field, e.g: some value
        self.valuekeyname = "value"     # name of the name key field, e.g: value

        parts = key.partition("/")
        if(parts[1] == "/"):
            self.keylistname = parts[0]      # tags
            key = parts[2]                   # AWS:Name

        parts = key.partition("?")
        if(parts[1] == "?"):
            self.valuekeyname = parts[2]
            key = parts[0]

        if(len(key) > 0):
            parts = key.partition(":")
            if(parts[1] == ":"):
                self.contextvalue = parts[0]     # AWS
                self.contextkeyname = "context"
                self.keyvalue = parts[2]         # Name
                self.keykeyname = "key"
            else:
                if(self.keylistname is not None):
                    self.keyvalue = parts[0]
                    self.keyname = "key"
                else:
                    self.valuekeyname = parts[0]

        # now we validate if any of the three parameters where defined with keyname#keyvalue
        if(self.contextvalue is not None):
            parts = self.contextvalue.partition("#")
            if(parts[1] == "#"):
                self.contextkeyname = parts[0]
                self.contextvalue = parts[2]

        if(self.keyvalue is not None):        
            parts = self.keyvalue.partition("#")
            if(parts[1] == "#"):
                self.keykeyname = parts[0]
                self.keyvalue = parts[2]

    def isTagSearch():
        return self.keylistname is not None

def getAttributeFromFirstMatch(attributeName, objectlist):
    "Iterates through a list of objects and returns first occurence of attributeName"
    "attributeName can also be a comma separated list. in that case the function returns an array of values of the object that first matches ALL attribute names"
    attributeNames = attributeName.split(",")

    for obj in objectlist:
        try:
            attributeValues = []
            if(obj is not None):
                for attribute in attributeNames:
                    attributeValue = obj[attribute]
                    if(attributeValue is not None):
                        attributeValues.append(attributeValue)

                if len(attributeValues) == len(attributeNames):
                    if(len(attributeValues) == 1):
                        return attributeValues[0]

                    return attributeValues
        except KeyError:
            x=1
    
    return "OBJECT DOESNT HAVE KEY " + attributeName

def jsonFindValuesByKey(jsonContent, key, matchValue, returnKey):
    "Traverses through the jsonContent object. Searches for the request key and returns the returnKey in case matchValue matches"
    return jsonFindValuesByKeyEx(jsonContent, key, matchValue, returnKey, None, None)

def jsonFindValuesByKeyEx(jsonContent, key, matchValue, returnKey, parentJsonNodename, parentJsonContent):
    "INTERNAL helper function for jsonFindValuesByKeyEx"
    if((key is not None) and (type(key) == str)):
        key = KeySearch(key)

    # we convert the regular match string to a compilied regex. we do it right here so we only have to do it once
    if((matchValue is not None) and (type(matchValue) == str)):
        try:
            matchValue = re.compile(matchValue)
        except:
            if global_doPrint:
                print(matchValue + " is NOT VALID regular expression")
            raise Exception("Regex Error", matchValue + " is NOT VALID regular expression") 

    # our final result list
    result = []
    if type(jsonContent) == str:
        jsonContent = json.loads(jsonContent)

    if type(jsonContent) is dict:
        # Only look into this dictionary if the parent node is what we want to find or if we dont care about the parent node name
        # when looking at a dictionary we either return when we found the first key match - or if we have two key matches in case KeySearch wants to search for Context and Name
        foundValueMatch = None
        foundContextMatch = key.contextvalue is None
        foundKeyMatch = key.keyvalue is None
        for jsonkey in jsonContent:
            # 1: We found the key and it is a list
            if ((type(jsonContent[jsonkey]) is list) and (jsonkey == key.keyvalue)):
                # if we found the key and it is a list we make sure that one list item matches
                if(matchValue is None):
                    foundKeyValueMatch = True
                else:
                    for listItem in jsonContent[jsonkey]:
                        if(matchValue.match(listItem)):
                            foundKeyValueMatch = True

                if foundKeyValueMatch is not None:
                    foundKeyValueMatch = getAttributeFromFirstMatch(returnKey, [jsonContent, parentJsonContent])

            #2: We found a key, it is a list and that is the valuekeyname we are looking for            
            if ((type(jsonContent[jsonkey]) is list) and (jsonkey == key.valuekeyname)):
                for listitem in jsonContent[jsonkey]:
                    if(matchValue.match(listitem)):
                        result.append(getAttributeFromFirstMatch(returnKey, [jsonContent, parentJsonContent]))

            # 2: we have a list (that doesnt match the key) and a dictionary (we dont care about matching keys as this is currently not supporrted)
            elif type(jsonContent[jsonkey]) in (list, dict):
                subResult = jsonFindValuesByKeyEx(jsonContent[jsonkey], key, matchValue, returnKey, jsonkey, jsonContent)
                if(len(subResult) > 0):
                    result.extend(subResult)

            # 3: we have a matching key on a regular value
            elif jsonkey == key.valuekeyname:
                # if we found the rigth key check if the value matches
                if(matchValue is None or matchValue.match(jsonContent[jsonkey])):
                    foundValueMatch = getAttributeFromFirstMatch(returnKey, [jsonContent, parentJsonContent])

            # 4: we have a matching context key in case user searches for context, e.g. in a tag
            elif (key.contextvalue is not None) and (jsonkey == key.contextkeyname):
                foundContextMatch = key.contextvalue == jsonContent[jsonkey]

            # 5: we have a matching key key in case user searches for context, e.g. in a tag
            elif (key.keyvalue is not None) and (jsonkey == key.keykeyname):
                foundKeyMatch = key.keyvalue == jsonContent[jsonkey]

        # if we iterated through a whole dictionary and found a matching value and, if necessary, matching context and key then we are good
        if (key.keylistname is None) or (key.keylistname == parentJsonNodename):
            if((foundValueMatch is not None) and foundContextMatch and foundKeyMatch):
                result.append(foundValueMatch)

    elif type(jsonContent) is list:
        for item in jsonContent:
            if type(item) in (list, dict):
                subResult = jsonFindValuesByKeyEx(item, key, matchValue, returnKey, parentJsonNodename, parentJsonContent)
                if(len(subResult) > 0):
                    result.extend(subResult)
    return result

def matchEntityName(entityName, listOfEntities):
    if(listOfEntities is None):
        return True

    if(type(listOfEntities) is str):
        return listOfEntities == entityName

    if(type(listOfEntities) is list):
        if(entityName in listOfEntities):
            return True

    return False;

def filterDataPointsForEntities(jsonDataPoints, entities):
    # Will iterate through the Data Points and return those metrics that match the entities. If entities == None we return all matching entities
    result = {}
    for entityDataPoint in jsonDataPoints:
        if matchEntityName(entityDataPoint, entities):
            result[entityDataPoint] = {}
            result[entityDataPoint]["dataPoints"] = jsonDataPoints[entityDataPoint]
    return result

def handleException(e):
    errorObject = {}
    if e.args:
        if len(e.args) == 2:
            errorObject[e.args[0]] = e.args[1]
        if len(e.args) == 1:
            errorObject["error"] = e.args[0]
    else:
        errorObject["exception"] = e

    print(errorObject)
    sys.exit(1)

# the following method is a pure TEST METHOD - allows me to test different combinations of parameters for my differnet use cases
def mainX():
    pass

    try:
        readConfig()

        # doConfig(False, ["dtcli", "config", "apitoken", "asdf"])
        # saveConfig()
        # queryDynatraceAPI(False, API_ENDPOINT_APPLICATIONS, "", "")
        #doEntity(False, ["dtcli", "ent", "app"], True)
        #doEntity(False, ["dtcli", "ent", "app", ".*", "displayName"], True)
        #doEntity(False, ["dtcli", "ent", "app", ".*", "displayName,entityId"], True)
        #doEntity(False, ["dtcli", "ent", "app", ".*easy.*", "displayName"], True)
        #doEntity(False, ["dtcli", "ent", "app", "www\.easytravel\.com", "displayName"], True)
        #doEntity(False, ["dtcli", "ent", "app", "www\.easytravel\.com", "entityId"], True)
        #doEntity(False, ["dtcli", "ent","host","tags/AWS:Name=et-demo.*", "displayName"], True)
        #doEntity(False, ["dtcli", "ent","host","tags/AWS:Category?value=DEMOABILITY", "displayName"], True)
        #doEntity(False, ["dtcli", "ent","host","tags/AWS:Name=.*", "value"], True)
        #doEntity(False, ["dtcli", "ent","host","tags/AWS:Name=.*", "entityId"], True)
        #doEntity(False, ["dtcli", "ent", "srv", "agentTechnologyType=JAVA", "displayName"], True)
        #doEntity(False, ["dtcli", "ent", "srv", "serviceTechnologyTypes=ASP.NET", "displayName"], True)
        #doEntity(False, ["dtcli", "ent", "srv", "serviceTechnologyTypes=ASP.NET", "entityId"], True)
        #doEntity(False, ["dtcli", "ent", "pg", "key=customizable", "entityId"], True)
        #doEntity(False, ["dtcli", "ent", "pg", "key=se-day", "displayName"], True)
        #doEntity(False, ["dtcli", "ent", "pg", "javaMainClasses=.*Bootstrap.*"], True)
        #doEntity(False, ["dtcli", "ent", "pg", "cloudFoundryAppNames=.*"], True)
        #doEntity(False, ["dtcli", "ent", "host", "ipAddresses=54\.86\..*"], True)
        #doEntity(False, ["dtcli", "ent", "pg", "softwareTechnologies/?type=TOMCAT"], True)

        #doTimeseries(False, ["dtcli", "ts", "list"], True)
        #doTimeseries(False, ["dtcli", "ts", "list", ".*"], True)
        #doTimeseries(False, ["dtcli", "ts", "list", "dimensions=APPLICATION"], True)
        #doTimeseries(False, ["dtcli", "ts", "list", ".*", "displayName"], True)
        #doTimeseries(False, ["dtcli", "ts", "list", "com.dynatrace.builtin:appmethod.useractionsperminute", "aggregationTypes"], True)
        #doTimeseries(False, ["dtcli", "ts", "describe", "com.dynatrace.builtin:appmethod.useractionsperminute"], True)
        #doTimeseries(False, ["dtcli", "ts", "query", "com.dynatrace.builtin:service.responsetime"], True)
        #doTimeseries(False, ["dtcli", "ts", "query", "com.dynatrace.builtin:appmethod.useractionsperminute[count%hour]"], True)
        #doTimeseries(False, ["dtcli", "ts", "query", "com.dynatrace.builtin:appmethod.useractionsperminute[count%hour]", "APPLICATION_METHOD-7B11AF03C396DCBC"], True)
        #doTimeseries(False, ["dtcli", "ts", "query", "com.dynatrace.builtin:app.useractionduration[avg%hour]", "APPLICATION-F5E7AEA0AB971DB1"], True)

        #doDQL(False, ["dtcli", "dql", "app", "www.easytravel.com", "app.useractions[count%hour],app.useractionduration[avg%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "host", ".*demo.*", "host.cpu.system[max%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "host", "tags/AWS:Name=et-demo.*", "host.cpu.system[max%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "host", "tags/AWS:Name=et-demo.*", "com.dynatrace.builtin:host.cpu.system[max%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "pg", "cloudFoundryAppNames=.*", "com.dynatrace.builtin:pgi.cpu.usage[avg%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "srv", "agentTechnologyType=JAVA", "service.responsetime[max%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "app", "www.easytravel.com", "app.useractions[count%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "app", "www.easytravel.com", "app.useractions[count%hour],app.useractionduration[avg%hour]"], True)
        #doDQL(False, ["dtcli", "dql", "appmethod", ".*Book.*", "appmethod.useractionduration[avg%hour]"], True)    
    
        # doEvent(False, ["dtcli", "evt"], False)
        # doEvent(False, ["dtcli", "evt", "query","from=360", "to=0"], False)
        # doEvent(False, ["dtcli", "evt", "query", "entityId=APPLICATION-F5E7AEA0AB971DB1"], False)
        # doEvent(False, ["dtcli", "evt", "query", "host", ".*demo.*"], False)
        doEvent(False, ["dtcli", "evt", "query", "from=360", "to=0", "app", "www.easytravelb2b.com"], False)
        # doEvent(False, ["dtcli", "evt", "push", "entityId", "APPLICATION-91A869F0065D216E", "deploymentName=My%20Test%20Deployment", "source=Dynatrace%20CLI", "deploymentVersion=1.0.0"], False)
        # doEvent(False, ["dtcli", "evt", "push", "host", ".*demo.*"], False)
        # doEvent(False, ["dtcli", "evt", "push", "host", "tags/AWS:Name=et-demo.*", "deploymentName=StageDeployment", "deploymentVersion=1.1"], False)
        # doEvent(False, ["dtcli", "evt", "push", "host", "tags/AWS:Name=et-demo.*", "start=12312421000", "deploymentName=StageDeployment", "deploymentVersion=1.1", "source=Jenkins", "ciBackLink=http://myjenkins", "remediationAction=http://myremediationaction", "mycustomprop=my%20custom%value"], False)
        # doEvent(False, ["dtcli", "evt", "push", "app", "www.easytravel.com", "eventType=CUSTOM_ANNOTATION", "annotationType=DNS route change", "source=OpsControl", "original=myoldurl.com", "changed=mynewurl.com"], False)

    except Exception as e:
        handleException(e)
    exit

# The REAL Main Method!
def main():
    
    try:
        readConfig()
        command = "usagae"
        doHelp = False
        if len(sys.argv) > 1:
            command = sys.argv[1]
        if command == "help":
            doHelp = True
            if len(sys.argv) > 2:
                command = sys.argv[2]
            else:
                command = "usage"

        if command == "ent" :
            doEntity(doHelp, sys.argv, True)
        elif command == "ts" :
            doTimeseries(doHelp, sys.argv, True)
        elif command == "config" :
            doConfig(doHelp, sys.argv)
        elif command == "prob" :
            doProblem(doHelp, sys.argv)
        elif command == "evt" :
            doEvent(doHelp, sys.argv, True)
        elif command == "dql" :
            doDQL(doHelp, sys.argv, True)
        else :
            doUsage(sys.argv)
    except Exception as e:
        handleException(e)
    exit

def readConfig():
    "Is reading stored configuration from the disk"
    global config
    if os.path.exists(dtconfigfilename):
        with open(dtconfigfilename) as json_data:
            config = json.load(json_data)

def saveConfig():
    "Stores configuration to disk"
    print("Current configuration stored in " + dtconfigfilename)
    with open(dtconfigfilename, 'w') as outfile:
        json.dump(config, outfile)        

def doUsage(args):
    "Just printing Usage"
    print("Usage: Dynatrace Command Line Interface")
    print("=========================================")
    print("dtcli <command> <options>")
    print("commands: ent=entities, ts=timerseries, prob=problems, evt=events, dql=Dynatrace Query Language, config")
    print("=========================================")
    print("To configure access token and Dynatrace REST Endpoint use command 'config'")
    print("For more information on a command use: dtcli help <command>")

def doEntity(doHelp, args, doPrint):
    "Allows you to query information about entities"
    if doHelp:
        if(doPrint):
            print("dtcli ent <type> <query> <resulttag>")
            print("type: app | srv | pg | host")
            print("Examples:")
            print("===================")
            print("dtcli ent app .*easyTravel.*")
            print("dtcli ent srv JourneyService")
            print("dtcli ent host tag/AWS:Name=et-demo-1-win1")
            print("dtcli ent host tag/Name=.*demo.*")
            print("dtcli ent srv serviceTechnologyTypes=ASP.NET discoveredName")
            print("dtcli ent app .*easyTravel.* displayName")
    else:
        entityTypes = ["app","srv","pg","host"]
        entityEndpoints = [API_ENDPOINT_APPLICATIONS, API_ENDPOINT_SERVICES, API_ENDPOINT_PROCESS_GROUPS, API_ENDPOINT_HOSTS]
        if (len(args) <= 2) or not operator.contains(entityTypes, args[2]):
            # Didnt provide the correct parameters - show help!
            doEntity(True, args, doPrint)
        else:
            # lets check our special token params
            doCheckTempConfigParams(args, 5)

            # As the Dynatrace API currently doesnt suppot all the filtering that we want to provide through this CLI we have to parse the response and filter in our script
            apiEndpoint = operator.getitem(entityEndpoints, operator.indexOf(entityTypes, args[2]))
            jsonContent = queryDynatraceAPI(True, apiEndpoint, "", "")
            resultTag = "entityId"

            if len(args) > 3:
                nameValue = parseNameValue(args[3], "displayName", "")
                if(len(args) > 4):
                    resultTag = args[4]
                elements = jsonFindValuesByKey(jsonContent, nameValue.name, nameValue.value, resultTag)
            else:
                elements = jsonFindValuesByKey(jsonContent, "displayName", None, resultTag)

            if(doPrint):
                print(elements)

            return elements;
    return None

def doTimeseries(doHelp, args, doPrint):
    "Allows you to query information about entities"
    if doHelp:
        if doPrint:
            print("dtcli ts <action> <options>")
            print("action: list | query | push")
            print("options for list: [*name*] [return key]")
            print("options for query: [tsid1,tsid2,...] ([entid1,entid2,...])")
            print("options for push: TODO")
            print("Examples:")
            print("===================")
            print("dtcli ts list")
            print("dtcli ts list .*")
            print("dtcli ts list .*response.*")
            print("dtcli ts list dimensions=APPLICATION")
            print("dtcli ts list *response.* displayName")
            print("dtcli ts describe com.dynatrace.builtin:appmethod.useractionsperminute")
            print("dtcli ts query jmx.tomcat.jdbc.pool:Active")
            print("dtcli ts query com.dynatrace.builtin:appmethod.useractionduration")
            print("dtcli ts query com.dynatrace.builtin:appmethod.useractionsperminute[count%hour]")
            print("dtcli ts query com.dynatrace.builtin:appmethod.useractionsperminute[count%hour] APP-ENTITY")
    else:
        actionTypes = ["list","query","push","describe"]
        action = None
        if (len(args) <= 2) or not operator.contains(actionTypes, args[2]):
            # Didnt provide the correct parameters - show help!
            doTimeseries(True, args, doPrint)
        else:
            action = operator.indexOf(actionTypes, args[2])

        if action == 0:   # list
            # lets check our special token params
            doCheckTempConfigParams(args, 5)

            jsonContent = queryDynatraceAPI(True, API_ENDPOINT_TIMESERIES, "", "")
            returnKey = "timeseriesId"
            matchValue = None
            matchKeyName = "displayName"
            if (len(args) > 4):
                returnKey = args[4]
            if (len(args) > 3):
                nameValue = parseNameValue(args[3], "displayName", "")
                matchValue = nameValue.value
                matchKeyName = nameValue.name

            elements = jsonFindValuesByKey(jsonContent, matchKeyName, matchValue, returnKey)
            print(elements)
        elif action == 1: # query
            # lets check our special token params
            doCheckTempConfigParams(args, 5)
        
            # build the query string for the timeseries id
            entities = None
            if(len(args) > 4):
                entities = args[4]
            if(len(args) > 3):
                timeseriesId = args[3]
                aggregation = "avg"
                timeframe = "hour"

                # "Allowed strings are: justtimeseries, timeseries[aggregagtion],, timeseries[aggregation%timeframe]"
                # now we check for name=value pair or just value
                beginBracket = timeseriesId.find("[")
                endBracket = timeseriesId.find("]")
                if(endBracket > beginBracket):
                    configuration = timeseriesId[beginBracket+1:endBracket]
                    timeseriesId = timeseriesId[0:beginBracket]
                    configParts = configuration.partition("%")
                    if(len(configParts[0]) > 0):
                        aggregation = configParts[0]
                    if(len(configParts[2]) > 0):
                        timeframe = configParts[2]

                # now lets query the timeframe API
                jsonContent = queryDynatraceAPI(True, API_ENDPOINT_TIMESERIES, "timeseriesId=" + timeseriesId + "&relativeTime=" + timeframe.lower() + "&aggregationType=" + aggregation.lower(), "")

                # We got our jsonContent - now we need to return the data for all Entities or the specific entities that got passed to us
                jsonContentResult = jsonContent["result"]
                if(jsonContentResult):
                    if(jsonContentResult["timeseriesId"] == timeseriesId):
                        measureResult = filterDataPointsForEntities(jsonContentResult["dataPoints"], entities)

                        # now we iterate through all Entitys and also get the name
                        for entity in measureResult:
                            measureResult[entity]["entityDisplayName"] = jsonContentResult["entities"][entity]
                            measureResult[entity]["unit"] = jsonContentResult["unit"]
                            measureResult[entity]["timeseriesId"] = jsonContentResult["timeseriesId"]

                        if doPrint:
                            print(measureResult)
                        return measureResult
            else:
                doTimeseries(True, args, doPrint)
        
        elif action == 2: # push
            print("TODO: implement ts push")
        elif action == 3: # describe
            # lets check our special token params
            doCheckTempConfigParams(args, 4)
        
            if(len(args) > 3):
                timeseriesId = args[3]
                jsonContent = queryDynatraceAPI(True, API_ENDPOINT_TIMESERIES, "", "")
                for timeseries in jsonContent:
                    if(timeseries["timeseriesId"] == timeseriesId):
                        if doPrint:
                            print(timeseries)
                        return timeseries
            else:
                doTimeseries(True, args, doPrint)
        else:
            doTimeseries(True, args, doPrint)

def doConfig(doHelp, args):
    
    if not doHelp and len(args) > 2 and args[2] == "revert":
        config["apitoken"] = "smpltoken"
        config["tenanthost"] = "smpljson"
        config["cacheupdate"] = -1
        print("Reverting back to local cached demo environment. Remember: only read-only operations work")
        saveConfig()
        return;

    if doHelp or len(args) < 4:
        print("You can set the following configuration options")
        print("apitoken <dynatracetoken>")
        print("tenanthost <yourdynatraceserver.domain>")
        print("cacheupdate -1 (only use cache), 0 (=never use cache), X (=update cache in X Minutes)")
        print("revert: will revert to local cache setting")
        print("Examples")
        print("==============")
        print("dtapi config apitoken ABCEDEFASDF tenanthost myurl.live.dynatrace.com cacheupdate 5")
        print("==============")
        print("Current Dynatrace Tenant: " + config["tenanthost"])
    else:
        # global config
        i = 2
        while i+2 <= len(args):
            configName = args[i]
            configValue = args[i+1]
            if configName == "apitoken":
                config["apitoken"] = configValue
            elif configName == "tenanthost":
                config["tenanthost"] = configValue
            elif configName == "cacheupdate":
                config["cacheupdate"] = configValue
            else:
                print("Configuration element '" + configName + "' not valid")
                doConfig(True, args)
                i = len(args)
            i = i+2

        saveConfig()

def doCheckTempConfigParams(args, argIndex):
    # special check for Dynatrace Token and Dynatrace URL. We allows this to pass in the credentials without having to go through config. this makes this query completely stateless
    if(len(args) > argIndex):
        config["tenanthost"] = args[argIndex]     
    if(len(args) > argIndex+1):
        config["apitoken"] = args[argIndex+1]     

def doDQL(doHelp, args, doPrint):
    # dql", "app", "www.easytravel.com", "app.useractions[avg%hour],app.useractionduration[avg%hour]
    "Allows you to query a list of metrics for a particular set of entity. This is a conventience option instead of using entity queries and then timeseries queries"
    if doHelp:
        print("dtcli dql <entitytype> <entity> <metrics> [dtUrl] [dtToken]")
        print("entitytype: app | appmethod | srv | pg | host")
        print("entity:     entityname")
        print("metrics:    metricname[aggr%time],metricname[aggr%time]")
        print("Examples:")
        print("===================")
        print("dtcli dql host .*demo.* host.cpu.system[max%hour]")
        print("dtcli dql host tags/AWS:Name=et-demo.* host.cpu.system[max%hour]")
        print("dtcli dql host tags/AWS:Name=et-demo.* com.dynatrace.builtin:host.cpu.system[max%hour]")
        print("dtcli dql pg cloudFoundryAppNames=.* com.dynatrace.builtin:pgi.cpu.usage[avg%hour]")
        print("dtcli dql srv agentTechnologyType=JAVA service.responsetime[max%hour]")
        print("dtcli dql app www.easytravel.com app.useractions[count%hour]")
        print("dtcli dql app www.easytravel.com app.useractions[count%hour],app.useractionduration[avg%hour]")
        print("dtcli dql appmethod .*Book.* appmethod.useractionduration[avg%hour]")
        print("-----")
        print("dtcli dql app www.easytravel.com app.useractions[count%hour] http://yourtenant.live.dynatrace.com ASESFEA12ASF")

    else:
        entityTypes = ["appmethod","app","srv","pg","host"]
        entityType = None
        if (len(args) <= 4) or not operator.contains(entityTypes, args[2]):
            # Didnt provide the correct parameters - show help!
            doDQL(True, args, doPrint)
            return;
        
        entityType = operator.indexOf(entityTypes, args[2])

        # lets check our special token params
        doCheckTempConfigParams(args, 5)

        # now lets get the data!
        if entityType == 0: # appmethod -> special handling as there is no queryable entitytype of appmethod
            allTimeseries = args[4].split(",")
            appMethodEntityMatch = re.compile(args[3])
            resultTimeseries = []
            for timeseries in allTimeseries:
                if(timeseries.find(":") <= 0):
                    timeseries = "com.dynatrace.builtin:" + timeseries
                resultTimeseriesForEntity = doTimeseries(False, ["dtcli", "ts", "query", timeseries], False)
                for timeseriesEntry in resultTimeseriesForEntity:
                    if appMethodEntityMatch.match(resultTimeseriesForEntity[timeseriesEntry]["entityDisplayName"]):
                        resultTimeseries.append({ timeseriesEntry : resultTimeseriesForEntity[timeseriesEntry]})

            if doPrint:
                print(resultTimeseries)

            return resultTimeseries;
        else:
            resultEntities = doEntity(False, ["dtcli", "ent", args[2], args[3]], False)
            resultTimeseries = []
            if(resultEntities is None):
                if doPrint:
                    print("No entities returned for that query")
            else:
                for entity in resultEntities:
                    # dtcli ts query com.dynatrace.builtin:appmethod.useractionsperminute[count%hour] APP-ENTITY
                    allTimeseries = args[4].split(",")
                    for timeseries in allTimeseries:
                        if(timeseries.find(":") <= 0):
                            timeseries = "com.dynatrace.builtin:" + timeseries
                        resultTimeseriesForEntity = doTimeseries(False, ["dtcli", "ts", "query", timeseries, entity], False)
                        resultTimeseries.append(resultTimeseriesForEntity)

            if doPrint:
                print(resultTimeseries)

            return resultTimeseries;

def doProblem(doHelp, args):
    print("TODO: problem")

def doEvent(doHelp, args, doPrint):
    # dql", "app", "www.easytravel.com", "app.useractions[avg%hour],app.useractionduration[avg%hour]
    "Allows you to query and push events from and to Dynatrace, e.g: tell Dynatrace about a Custom Deployment event on a service"
    if doHelp:
        print("dtcli evt <action> <entity> <options> [dtUrl] [dtToken]")
        print("action:  query | push")
        print("entity:  either entity ids or queries of particular entity types")
        print("         - ent ABCD1231231: this specifies exactly this entity")
        print("         - host .*demo.*: this will query the hosts that match that name")
        print("options: list of name/value pairs. The only mandatory option is the entityId. Here is a list of additional options: ")
        print("         - start, end: Timestamp of start/end of event")
        print("         - deploymentName, deploymentVersion, deploymentProject: any textual representation of your deployment")
        print("         - source: should be the name of your deployment automation tool or CI/CD pipeline, e.g: Jenkins, Electric Cloud, AWS CodeDeploy ...")
        print("         - ciBackLink, remediationAction: links to the pipeline or remedating action")
        print("         - eventType: either CUSTOM_DEPLOYMENT or CUSTOM_ANNOTATION")
        print("         - any other name/value pair will be passed as custom properties")
        print("Examples:")
        print("===================")
        print("dtcli evt query")
        print("dtcli evt query entityId ABCDEF12345")
        print("dtcli evt query host .*demo.*")
        print("dtcli evt query eventType=SERVICE_RESPONSE_TIME_DEGRADED from=60 to=0")
        print("dtcli evt push entityId ABCDEF12345")
        print("dtcli evt push host .*demo.*")
        print("dtcli evt push host tags/Environment=Staging deploymentName=StageDeployment deploymentVersion=1.1")
        print("dtcli evt push ent ABCDEF12345 start=1234124123000 end=1231230000 deploymentName=StageDeployment deploymentVersion=1.0 deploymentProject=easyTravel source=Jenkins ciBackLink=http://myjenkins remediationAction=http://myremediationaction")
        print("dtcli evt push ent ABCDEF12345,BCDEF3333 deploymentName=StageDeployment source=Jenkins mycustomproperty=my%20custom%value someotherpropoerty=someothervalue")
        print("-----")
        print("dtcli evt push ent ABCDEF12345 http://yourtenant.live.dynatrace.com ASESFEA12ASF")
    else:
        actionTypes = ["query","push"]
        action = None
        if (len(args) <= 2) or not operator.contains(actionTypes, args[2]):
            # Didnt provide the correct parameters - show help!
            doEvent(True, args, doPrint)
            return;
        
        action = operator.indexOf(actionTypes, args[2])
        if action == 0: #query
            # we need to parse input parameters such as from, to, eventType and entityId
            query = {"eventType" : None, "from" : None, "to" : None, "entityId" : None}
            queryFields = ["eventType","from","to","entityId"]
            entityQueryItems = ["dtcli", "ent"]

            # iterate through all parameters and parse out those that are standard parameters -> rest will be passed to the Entity Query
            for arg in args[3::]:
                nameValue = arg.split("=")
                if(len(nameValue) > 1 and operator.contains(queryFields, nameValue[0])):
                    query[nameValue[0]] = nameValue[1]
                else:
                    entityQueryItems.append(arg)

            # lets call the entity query and take the first entityId
            if len(entityQueryItems) > 2:
                resultEntity = doEntity(False, entityQueryItems, doPrint)
                if resultEntity and len(resultEntity) > 0:
                    query["entityId"] = resultEntity[0]

            # for timestamps we allow to specify either a full timestamp or Minutes.
            if query["from"] is not None and int(query["from"]) < 1000000:
                query["from"] = str(1000 * int(datetime.datetime.now().timestamp() - int(query["from"])*60))
            if query["to"] is not None and int(query["to"]) < 1000000:
                query["to"] = str(1000 * int(datetime.datetime.now().timestamp() - int(query["to"])*60))

            # now - lets build the actual query string
            queryString = ""
            for objAttr in query:
                if query[objAttr] is not None:
                    queryString = queryString + "&" + objAttr + "=" + query[objAttr]
            
            if len(queryString) > 0:
                queryString = queryString[1:]
            events = queryDynatraceAPI(True, API_ENDPOINT_EVENTS, queryString, "")

            print(events)
        elif action == 1: # push
            # we start with an almost empty event.
            coreEventFields = ["start","end","deploymentName","deploymentVersion","deploymentProject","source","ciBackLink","remediationAction","eventType","annotationType","annotationDescription"]
            event = {
                "start" : None,
                "end" : None,
                "source" : "Dynatrace CLI",
                "eventType" : "CUSTOM_DEPLOYMENT",
                "attachRules" : { "entityIds" : []},
                "customProperties" : {}
            }
        
            # the minimum requirement is information about the entities
            if(len(args) <= 4):
                doEvent(True, args, doPrint)
                return

            if(args[3] == "entityId"):
                event["attachRules"]["entityIds"].append(args[4])
            else:
                foundEntities = doEntity(False, ["dtcli", "ent", args[3], args[4]], False)
                if(len(foundEntities) <= 0):
                    raise Exception("Error", "No Entities found that match query")
                    return;
                event["attachRules"]["entityIds"] = foundEntities
            
            # lets parse through all name/value pairs
            for arg in args[5::]:
                nameValue = arg.split("=")
                if(len(nameValue) != 2):
                    raise Exception("Error", "Invalid parameter passed: " + arg)
                    return
                if(operator.contains(coreEventFields, nameValue[0])):
                    event[nameValue[0]] = urllib.parse.unquote(nameValue[1])
                else:
                    event["customProperties"][nameValue[0]] = urllib.parse.unquote(nameValue[1])

            # Make sure that Start / End are correctly set. If not specified we set it to NOW(). If end is not set we set it to start
            if event["start"] is None:
                event["start"] = str(1000 * int(datetime.datetime.now().timestamp()))
            if event["end"] is None:
                event["end"] = str(1000 * int(datetime.datetime.now().timestamp()))

            if doPrint:
                print(event)

            # lets push the event to Dynatrace            
            response = queryDynatraceAPI(False, API_ENDPOINT_EVENTS, "", event)
            if doPrint:
                print(response)

def doTag(doHelp, args):
    print("TODO Tag")

if __name__ == "__main__": main()