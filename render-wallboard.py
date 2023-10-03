#!/usr/bin/python

#
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

import boto3
from boto3.dynamodb.conditions import Key,Attr
import os
import time
import logging
import string
import re
import json

#
# Things to configure
#
DDBTableName    = os.environ.get('WallboardTable', 'ConnectWallboard')
ConfigTimeout   = os.environ.get('ConfigTimeout', 300) # How long we wait before grabbing the config from the database
RealtimeTimeout = 5 # How long before in between polling the real-time API
Table           = boto3.resource('dynamodb').Table(DDBTableName)

logging.basicConfig()
Logger = logging.getLogger()
Logger.setLevel(logging.WARNING)

#
# Sane defaults for new wallboards in case specific settings aren't given
#
DefaultSettings = {
    'AlertBackgroundColour': 'red', 
    'WarningBackgroundColour': 'yellow', 
    'TextColour': 'black', 
    'Font': 'sans-serif',
    'BackgroundColour': 'lightgrey'
}

#
# Global state
#
LastRun         = 0
LastRealtimeRun = 0
Settings        = {}
Cells           = {}
Thresholds      = {}
AgentStates     = {}
Data            = {}
Calculations    = {}
DataSources     = {}
NextAgent       = 0
SortedAgentList = []
FullAgentNames  = {}

Logger          = logging.getLogger()

#
# List of valid metrics we can retrieve
#
MetricUnitMapping = {
    'AGENTS_AVAILABLE': 'COUNT',
    'AGENTS_ONLINE': 'COUNT',
    'AGENTS_ON_CALL': 'COUNT',
    'AGENTS_STAFFED': 'COUNT',
    'AGENTS_AFTER_CONTACT_WORK': 'COUNT',
    'AGENTS_NON_PRODUCTIVE': 'COUNT',
    'AGENTS_ERROR': 'COUNT',
    'CONTACTS_IN_QUEUE': 'COUNT',
    'OLDEST_CONTACT_AGE': 'SECONDS',
    'CONTACTS_SCHEDULED': 'COUNT'                                 
  }

#
# List of functions that can be used in calculations - probably not complete
# so should be added to as necessary
#
FunctionList = ['round', 'int', 'float', 'min', 'max', 'sum', 'ord', 'pow']

def GetConfiguration(WallboardName):
    global LastRun,ConfigTimeout,DDBTableName,Logger,Table,Settings,Cells,Thresholds,AgentStates,Calculations,DataSources
    
    #
    # We only want to retrieve the configuration for the wallboard if we haven't
    # retrieved it recently or it hasn't previously been loaded.
    #
    GetConfig = False
    if WallboardName not in Settings:
        LastRun = time.time()
        GetConfig = True
        Logger.info(f'No config loaded for {WallboardName} retrieving')
    else:
        Logger.info(f'Last run at {LastRun}, timeout is {ConfigTimeout}, now is {time.time()}')
    
        if time.time() > LastRun+ConfigTimeout:
            LastRun = time.time()
            GetConfig = True
            Logger.info('  Wallboard config needs refreshing')
        else:
            Logger.info('  Within timeout period - no config refresh')
    
    if not GetConfig: return True

    #
    # All relevant wallboard information (how it is to be formatted, threshold
    # details, etc.) all have a primary partition key of the name of the
    # wallboard.
    #
    try:
        Response = Table.query(KeyConditionExpression=Key('Identifier').eq(WallboardName))
        ConfigList = Response
    except Exception as e:
        Logger.error(f'DynamoDB error: {e}')
        return False

    if len(Response['Items']) == 0:
        Logger.error(f'Did not get any configuration for wallboard {WallboardName}')
        return False

    while 'LastEvaluatedKey' in Response:
        try:
            Response = Table.query(ExclusiveStartKey=response['LastEvaluatedKey'])
            ConfigList.update(Response)
        except Exception as e:
            Logger.error(f'DynamoDB error: {e}')
            break

    LocalSettings     = DefaultSettings.copy()
    LocalThresholds   = {}
    LocalCells        = {}
    LocalAgentStates  = {}
    LocalCalculations = {}
    LocalDataSources  = {}
    for Item in ConfigList['Items']:
        if Item['RecordType'] == 'Settings':
            for Config in Item:
                LocalSettings[Config] = Item[Config]
        elif Item['RecordType'][:11] == 'Calculation':
            if 'Formula' not in Item:
                Logger.warning(f'Formula not set for {Item["RecordType"]} in wallboard {WallboardName} - ignored')
                continue
            LocalCalculations[Item['Name']] = Item['Formula']
        elif Item['RecordType'][:4] == 'Cell':
            if 'Address' not in Item:
                Logger.warning(f'Cell address not set for {Item["RecordType"]} in wallboard {WallboardName} - ignored')
                continue
            LocalCells[Item['Address']] = Item
        elif Item['RecordType'][:9] == 'Threshold':
            if 'Name' not in Item:
                Logger.warning(f'Threshold name not set for {Item["RecordType"]} in wallboard {WallboardName} - ignored')
                continue
            LocalThresholds[Item['Name']] = Item
        elif Item['RecordType'][:10] == 'AgentState':
            if 'StateName' not in Item:
                Logger.warning(f'Agent state name not set for {Item["RecordType"]} in wallboard {WallboardName} - ignored')
                continue
            LocalAgentStates[Item['StateName']] = Item['BackgroundColour']
        elif Item['RecordType'][:10] == 'DataSource':
            if 'Name' not in Item:
                Logger.warning(f'Data source reference not set for {Item["RecordType"]} in wallboard {WallboardName} - ignored')
                continue
            
            Metric = Item['Reference'].split(':')[2]
            if Metric not in MetricUnitMapping: continue # Ignore non real-time metrics
            LocalDataSources[Item['Name']] = Item['Reference']

    Settings[WallboardName]     = LocalSettings
    Cells[WallboardName]        = LocalCells
    Thresholds[WallboardName]   = LocalThresholds
    AgentStates[WallboardName]  = LocalAgentStates
    Calculations[WallboardName] = LocalCalculations
    DataSources[WallboardName]  = LocalDataSources
    
    return True

def GetData():
    global Logger,Data,NextAgent,SortedAgentList,FullAgentNames
    
    SortedAgentList = []
    NextAgent       = 0

    #
    # All data retrieved from other sources is stored in the DDB table with
    # the primary partition key of "Data" and a primary sort key of the name
    # of the value that has been stored.
    # We could get back numerical data (stored as a string) or agent state
    # details.
    #
    try:
        Response = Table.query(KeyConditionExpression=Key('Identifier').eq('Data'))
        AllData = Response
    except Exception as e:
        Logger.error(f'DynamoDB error: {e}')
        return
    
    if len(Response['Items']) == 0:
        Logger.error('Did not get any data from DynamoDB')
        return

    while 'LastEvaluatedKey' in Response:
        try:
            Response = Table.query(ExclusiveStartKey=response['LastEvaluatedKey'])
            AllData.update(Response)
        except Exception as e:
            Logger.error(f'DynamoDB error: {e}')
            break

    for Item in AllData['Items']:
        Data[Item['RecordType']] = Item['Value']
        if 'AgentARN' in Item:
            SortedAgentList.append(Item['RecordType'])
            if 'FullAgentName' in Item:
                FullAgentNames[Item['RecordType']] = Item['FullAgentName']
        
    #
    # We want the agents in alphabetical order
    #
    SortedAgentList.sort()
    
def StoreMetric(ConnectARN, QueueARN, MetricName, Value):
    global DataSources,Data,Logger

    SourceString = f'{ConnectARN}:{QueueARN}:{MetricName}'

    for Wallboard in DataSources:
        for Source in DataSources[Wallboard]:
            if DataSources[Wallboard][Source] == SourceString:
                Data[Source] = str(int(Value))
                Logger.info(f'Storing {Data[Source]} in {Source}')
                return

    Logger.warning(f'Could not find {SourceString} in DataSources')

def GetRealtimeData():
    global Logger,LastRealtimeRun,Data,DataSources,MetricUnitMapping

    #
    # We only want to poll the real-time API every so often.
    #
    Logger.info(f'Last real-time poll at {LastRealtimeRun}, timeout is {RealtimeTimeout}, now is {time.time()}')
    
    if time.time() < LastRealtimeRun+RealtimeTimeout: return
    LastRealtimeRun = time.time()

    Connect = boto3.client('connect')
    
    #
    # Even though data sources are defined per wallboard we will always retrieve
    # all of them each time as they may be cross-referenced on other wallboards.
    #
    # First build a list of information we need from the API.
    #
    ConnectList = {}
    for WallboardName in DataSources:
        for Item in DataSources[WallboardName]:
            if Item not in Data: Data[Item] = '0'

            (ConnectARN,QueueARN,Metric) = DataSources[WallboardName][Item].split(':')
 
            if ConnectARN not in ConnectList: ConnectList[ConnectARN] = {}
            if QueueARN not in ConnectList[ConnectARN]: ConnectList[ConnectARN][QueueARN] = []
            ConnectList[ConnectARN][QueueARN].append({'Name':Metric, 'Unit':MetricUnitMapping[Metric]})

    #
    # Now call the API for each Connect instance we're interested in.
    #
    for Instance in ConnectList:
        Logger.info(f'Retrieving real-time data from {Instance}')
        
        MetricList = []
        for Queue in ConnectList[Instance]:
            MetricList += ConnectList[Instance][Queue]

        try:
            Response = Connect.get_current_metric_data(
                           InstanceId=Instance,
                           Groupings=['QUEUE'],
                           Filters={'Queues':list(ConnectList[Instance].keys())},
                           CurrentMetrics=MetricList)
        except Exception as e:
            Logger.error(f'Failed to get real-time data: {e}')
            continue

        if 'MetricResults' not in Response: continue
        for Collection in Response['MetricResults']:
            QueueARN   = Collection['Dimensions']['Queue']['Id']

            for Metric in Collection['Collections']:
                MetricName  = Metric['Metric']['Name']
                MetricValue = Metric['Value']
                StoreMetric(Instance, QueueARN, MetricName, MetricValue)

def DoCalculation(WallboardName, Reference):
    global Logger,Data,Calculations,FunctionList
    
    Result = '0' # All values are stored as strings when they come out of DDB

    #
    # Split the calculation based on mathemetical operators
    #
    CalcArray = re.split('(\+|\*|\-|\/|\(|\)|,)', Calculations[WallboardName][Reference])

    # Substitute in the values for the labels in the calculation
    #
    Index = 0
    for Index in range(0, len(CalcArray)):
        if CalcArray[Index] in string.punctuation: continue
        if CalcArray[Index] in FunctionList: continue
        if CalcArray[Index][0] in string.digits: continue
    
        if CalcArray[Index] in Data:
            CalcArray[Index] = Data[CalcArray[Index]]
        else:
            Logger.warning(f'Calc: Could not find reference {CalcArray[Index]}')
            CalcArray[Index] = '0'

    CalcString = ''.join(CalcArray)
    Logger.info(f'Calculation for {Reference}: {Calculations[WallboardName][Reference]} -> {CalcString}')
    
    try:
        Result = str(eval(CalcString))
    except Exception as e:
        Logger.error(f'Could not eval {Reference}: {Calculations[WallboardName][Reference]} -> {CalcString} : {e}')
        
    return Result
    
def CheckThreshold(WallboardName, ThresholdReference):
    global Settings,Data,Thresholds,Logger,Calculations
    
    #
    # For the given data reference, check for any threshold details and then
    # return the right colour (which will be used for the cell background when
    # displayed). We have warning thresholds (above and below) and error
    # thresholds (above and below).
    #
    Colour         = ''
    ThresholdLevel = 'Normal' # Additional flag for JSON data return

    if ThresholdReference not in Thresholds[WallboardName]:
        Logger.warning(f'Threshold reference {ThresholdReference} does not exist for wallboard {WallboardName}')
        return Colour, ThresholdLevel

    Threshold = Thresholds[WallboardName][ThresholdReference]
    if 'Reference' not in Threshold:
        Logger.warning(f'No data reference present in threshold {ThresholdReference} for wallboard {WallboardName}')
        return Colour, ThresholdLevel

    if Threshold['Reference'] not in Data:
        if Threshold['Reference'] in Calculations:
            Data[Threshold['Reference']] = DoCalculation(WallboardName, Threshold['Reference'])
        else:
            Logger.warning(f'Data reference {Threshold["Reference"]} in threshold {ThresholdReference} does not exist for wallboard {WallboardName}')
            return Colour, ThresholdLevel

    if 'WarnBelow' in Threshold:
        if int(Data[Threshold['Reference']]) < int(Threshold['WarnBelow']):
             Colour = Settings[WallboardName]['WarningBackgroundColour']
             ThresholdLevel = 'Warning'
    if 'AlertBelow' in Threshold:
        if int(Data[Threshold['Reference']]) < int(Threshold['AlertBelow']):
             Colour = Settings[WallboardName]['AlertBackgroundColour']
             ThresholdLevel = 'Alert'
    if 'WarnAbove' in Threshold:
        if int(Data[Threshold['Reference']]) > int(Threshold['WarnAbove']):
             Colour = Settings[WallboardName]['WarningBackgroundColour']
             ThresholdLevel = 'Warning'
    if 'AlertAbove' in Threshold:
        if int(Data[Threshold['Reference']]) > int(Threshold['AlertAbove']):
             Colour = Settings[WallboardName]['AlertBackgroundColour']
             ThresholdLevel = 'Alert'

    return Colour, ThresholdLevel

def GetNextAgent(GetActive, JSONFlag=False):
    global SortedAgentList,NextAgent,FullAgentNames
    
    #
    # When we need to display a list of all agents currently active, this
    # function returns the names one-by-one so that the caller can fill in the
    # cells in the wallboard table.
    #
    AgentName = ''
    HTML      = ''
    JSON      = {}
    
    if NextAgent >= len(SortedAgentList): # No more agents to list
        if JSONFlag:
            return JSON, ''
        else:
            return HTML, ''

    if not GetActive: # Return the next agent whether active in the system or not
        AgentName = SortedAgentList[NextAgent]
        NextAgent += 1
    else: # Return the next active agent
        while NextAgent < len(SortedAgentList):
            AgentState = Data[SortedAgentList[NextAgent]]
            if len(AgentState) == 0 or AgentState == 'Logout':
                NextAgent += 1
                continue
            
            AgentName = SortedAgentList[NextAgent]
            NextAgent += 1
            break
        
    if len(AgentName) == 0: # No agent found
        if JSONFlag:
            return JSON, ''
        else:
            return HTML, ''
    
    if JSONFlag:
        if AgentName in FullAgentNames: # Just in case we didn't find a full name for this agent
            JSON['FullAgentName'] = FullAgentNames[AgentName]
        JSON['AgentState'] = Data[AgentName]

        return JSON, AgentName
    else:
        if AgentName in FullAgentNames: # Just in case we didn't find a full name for this agent
            HTML += f'<div class="text">{FullAgentNames[AgentName]}</div>'
        HTML += f'<div class="data">{Data[AgentName]}</div>'

        return HTML, Data[AgentName] # Return the state so we can set the cell background colour

def RenderCell(WallboardName, Row, Column):
    global AgentStates,Thresholds,Logger,Data,Calculations
    
    #
    # Given a particular cell, figure out the right colours and cell contents.
    # A cell may contain static text, a number or agent state derived directly
    # from the data read from the DDB table, or it may be a calculation we need
    # to perform. Also need to ensure that thresholds are checked for numerical
    # values where present.
    #
    Address      = f'R{Row}C{Column}'
    HTML         = ''
    AgentDetails = ''
    
    if Address not in Cells[WallboardName]: return HTML
    Cell = Cells[WallboardName][Address]
    LocalStates = AgentStates[WallboardName]

    Style = [] 
    Style.append('border: 1px solid black; padding: 5px;')
    if 'TextColour' in Cell: Style.append(f'color: {Cell["TextColour"]};')
    if 'TextSize'   in Cell: Style.append(f'font-size: {Cell["TextSize"]}px;')

    Background = ''
    if 'Reference' in Cell:
        State = ''
        if Cell['Reference'] in Calculations[WallboardName]: # We need to calculate this one
            Data[Cell['Reference']] = DoCalculation(WallboardName, Cell['Reference'])
        elif Cell['Reference'].lower() in Data: # Data already exists
            State = Data[Cell['Reference']]
        elif Cell['Reference'] == '=allagents': # Any agent at all
            (AgentDetails, State) = GetNextAgent(False)
        elif Cell['Reference'] == '=activeagents': # Active agents only
            (AgentDetails, State) = GetNextAgent(True)

        if len(State) > 0:
            State = State.lower()
            if State in LocalStates:
                Background = LocalStates[State]

    if 'ThresholdReference' in Cell:
        (NewBackground,Level) = CheckThreshold(WallboardName, Cell['ThresholdReference'])
        if len(NewBackground) > 0: Background = NewBackground
        
    if len(Background) == 0:
        if 'BackgroundColour' in Cell: Background = Cell['BackgroundColour']
    if len(Background) > 0: Style.append(f'background: {Background};')

    Tag = f'R{Row}C{Column}'
    HTML += f'<td label="{Tag}" class="{Tag}"'
    if 'Rows'     in Cell: HTML += f' rowspan="{Cell["Rows"]}"'
    if 'Columns'  in Cell: HTML += f' colspan="{Cell["Columns"]}"'
    if len(Style) > 0: HTML += f' style="{" ".join(Style)}"'
    HTML += '>'

    if 'Text' in Cell: HTML += f'<div class="text">{Cell["Text"]}</div>'
    if 'Reference' in Cell:
        if Cell['Reference'] in Data:
            HTML += f'<div class="data">{Data[Cell["Reference"]]}</div>'
        elif Cell['Reference'] == '=allagents' or Cell['Reference'] == '=activeagents':
            HTML += AgentDetails
        else:
            Logger.warning(f'Data reference {Cell["Reference"]} in cell {Address} does not exist for wallboard {WallboardName}')

    HTML += '</td>'
    return HTML

def RenderHTML(WallboardName):
    global Settings

    #
    # Build the containing table for the wallboard and then render each cell
    # according to the wallboard configuration.
    #
    LocalSettings = Settings[WallboardName]
    HTML = ''

    HTML += f'<table label="ConnectWallboard{LocalSettings["Identifier"].replace(" ", "")}"'
    HTML += ' style="border: 1px solid black; border-collapse: collapse; margin-left: auto; margin-right: auto; text-align: center;'
    if 'TextColour'       in LocalSettings: HTML += f' color: {LocalSettings["TextColour"]};'
    if 'BackgroundColour' in LocalSettings: HTML += f' background: {LocalSettings["BackgroundColour"]};'
    if 'TextSize'         in LocalSettings: HTML += f' font-size: {LocalSettings["TextSize"]}px;'
    if 'Font'             in LocalSettings: HTML += f' font-family: {LocalSettings["Font"]};'
    HTML += f'" class="wallboard-{WallboardName}">\n'

    for Row in range(1, int(LocalSettings['Rows'])+1):
        HTML += ' <tr>'
        for Column in range(1, int(LocalSettings['Columns'])+1):
            HTML += RenderCell(WallboardName, Row, Column)
        HTML += '</tr>\n'

    HTML += '</table>\n'

    return HTML

def GetRawCellData(WallboardName, Row, Column):
    global AgentStates,Thresholds,Logger,Data,Calculations
    
    #
    # As with the HTML render, Given a particular cell, get the data from the
    # appropriate source but return it as a dictionary.
    #
    Address = f'R{Row}C{Column}'
    JSON = {}
    
    if Address not in Cells[WallboardName]: return JSON
    Cell = Cells[WallboardName][Address]

    #
    # Agent state is sent back in a different place for a JSON return so we
    # don't do that here.
    #
    if 'Reference' in Cell:
        if Cell['Reference'] == '=allagents' or Cell['Reference'] == '=activeagents':
            return JSON

    #
    # As with the whole table the front-end process can ignore the formatting "hints".
    #
    Format = {}
    if 'BackgroundColour' in Cell: Format['BackgroundColour'] = Cell['BackgroundColour']
    if 'TextColour' in Cell:       Format['Colour'] = Cell['TextColour']
    if 'TextSize'   in Cell:       Format['TextSize'] = Cell['TextSize']

    if 'Reference' in Cell:
        if Cell['Reference'] in Calculations[WallboardName]: # We need to calculate this one
            Data[Cell['Reference']] = DoCalculation(WallboardName, Cell['Reference'])

    if 'ThresholdReference' in Cell:
        (Background,Level) = CheckThreshold(WallboardName, Cell['ThresholdReference'])
        if len(Background) > 0: Format['BackgroundColour'] = Background
        JSON['Threshold'] = Level
        
    if 'Rows'     in Cell: Format['RowSpan'] = Cell['Rows']
    if 'Columns'  in Cell: Format['ColSpan'] = Cell['Columns']

    JSON['Format'] = Format

    if 'Text' in Cell: JSON['Text'] = Cell['Text']
    if 'Reference' in Cell:
        JSON['Metric'] = Cell['Reference']
        if Cell['Reference'] in Data:
            JSON['Value'] = Data[Cell['Reference']]

    return JSON
    
def RenderJSON(WallboardName):
    global Settings

    #
    # Build a dictionary with all of the data in it - basically the same as
    # the HTML table but in JSON so that the front end can render the data
    # however it likes.
    #
    LocalSettings = Settings[WallboardName]
    JSON = {}

    #
    # The settings provided are for appearance only so the front end can
    # ignore these and render the data in whatever format is appropriate.
    #
    JSON['Settings'] = {}
    if 'TextColour'              in LocalSettings: JSON['Settings']['TextColour'] = LocalSettings['TextColour']
    if 'BackgroundColour'        in LocalSettings: JSON['Settings']['BackgroundColour'] = LocalSettings['BackgroundColour']
    if 'TextSize'                in LocalSettings: JSON['Settings']['FontSize'] = LocalSettings['TextSize']
    if 'Font'                    in LocalSettings: JSON['Settings']['Font'] = LocalSettings['Font']
    if 'AlertBackgroundColour'   in LocalSettings: JSON['Settings']['AlertBackgroundColour'] = LocalSettings['AlertBackgroundColour']
    if 'WarningBackgroundColour' in LocalSettings: JSON['Settings']['WarningBackgroundColour'] = LocalSettings['WarningBackgroundColour']
    JSON['Settings']['AgentStateList'] = AgentStates[WallboardName]

    #
    # Get all the agent states.
    #
    JSON['AgentStates'] = {}
    (AgentState,AgentName) = GetNextAgent(False, JSONFlag=True)
    while len(AgentName) > 0:
        JSON['AgentStates'][AgentName] = AgentState
        (AgentState,AgentName) = GetNextAgent(False, JSONFlag=True)

    #
    # Now the rest of the data for this wallboard.
    #
    JSON['WallboardData'] = {}
    for Row in range(1, int(LocalSettings['Rows'])+1):
        for Column in range(1, int(LocalSettings['Columns'])+1):
            CellData = GetRawCellData(WallboardName, Row, Column)
            if len(CellData): JSON['WallboardData'][f'R{Row}C{Column}'] = CellData

    return json.dumps(JSON)

def lambda_handler(event, context):
    GetData()

    Response = {}
    Response['statusCode'] = 200
    Response['headers']    = {'Access-Control-Allow-Origin': '*'}

    if str(type(event['queryStringParameters'])).find('dict') == -1 or 'Wallboard' not in event['queryStringParameters']:
        Response['body'] = '<div class="error">No wallboard name specified</div>'
        return Response

    WallboardName = event['queryStringParameters']['Wallboard']
    if GetConfiguration(WallboardName):
        GetRealtimeData()

        JSONFlag = event['queryStringParameters'].get('json')
        if JSONFlag:
            OutputData = RenderJSON(WallboardName)
        else:
            OutputData = RenderHTML(WallboardName)
    else:
        OutputData = f'<div class="error">Wallboard {WallboardName} not found</div>'

    Response['body'] = OutputData
    return Response
