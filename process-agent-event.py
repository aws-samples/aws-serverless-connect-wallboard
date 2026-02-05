#!/usr/bin/python

#
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
from boto3.dynamodb.conditions import Attr
import base64
import json
import os
import logging

DDBTableName = os.environ.get('WallboardTable', 'ConnectWallboard')
Table        = boto3.resource('dynamodb').Table(DDBTableName)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def SaveStateToDDB(Username, FullAgentName, AgentARN, State):
    global Table
    
    Data = {}
    Data['Identifier']    = 'Data'
    Data['RecordType']    = Username
    Data['Value']         = State
    Data['AgentARN']      = AgentARN
    Data['FullAgentName'] = FullAgentName
    
    try:
        Table.put_item(TableName=DDBTableName, Item=Data)
    except Exception as e:
        logger.error(f'DDB put error: {e}')

def SaveStateUsingARN(AgentARN, State):
    global Table
    
    try:
        # Scan the table looking for the agent ARN
        Expression = Attr('AgentARN').eq(AgentARN)
        Response = Table.scan(FilterExpression=Expression)
    except Exception as e:
        logger.error(f'DDB scan error: {e}')
        return
    
    if len(Response['Items']) > 0:
        logger.info(f'AgentARN: {AgentARN} = {Response["Items"][0]["RecordType"]}')
        SaveStateToDDB(Response['Items'][0]['RecordType'], Response['Items'][0]['FullAgentName'], AgentARN, State)
    
def lambda_handler(event, context):
    for RawPayload in event['Records']:
        AgentEvent = json.loads(base64.b64decode(RawPayload['kinesis']['data']))
        EventType = AgentEvent['EventType']
        AgentARN = AgentEvent['AgentARN']
        logger.info('Event type: {EventType} AgentARN: {AgentARN}')
        
        if EventType == 'LOGIN': # We don't really need to do this but just in case...
            SaveStateUsingARN(AgentARN, 'Login')   
            continue
        if EventType == 'LOGOUT':
            SaveStateUsingARN(AgentARN, 'Logout')   
            continue
        if EventType == 'STATE_CHANGE':
            State     = AgentEvent['CurrentAgentSnapshot']['AgentStatus']['Name']
            AgentName = f'{AgentEvent["CurrentAgentSnapshot"]["Configuration"]["FirstName"]} {AgentEvent["CurrentAgentSnapshot"]["Configuration"]["LastName"]}'
            Username  = AgentEvent['CurrentAgentSnapshot']['Configuration']['Username']

            if State == 'Available':
                Contacts = AgentEvent['CurrentAgentSnapshot']['Contacts']

                if Contacts:
                    for Contact in Contacts:
                        ContactState = Contact['State']
                        if ContactState == 'CONNECTED':
                            State = 'On Contact'
                        elif ContactState == 'CONNECTING':
                            State = 'On Contact'
                        elif ContactState == 'PENDING':
                            State = 'On Contact'
                        elif ContactState == 'CONNECTED_ONHOLD':
                            State = 'On Hold'
                        elif ContactState == 'MISSED':
                            State = 'Missed'
                        elif ContactState == 'PAUSED':
                            State = 'Paused'
                        elif ContactState == 'REJECTED':
                            State = 'Rejected'
                        elif ContactState == 'ENDED':
                            State = 'After Call Work'
                        elif ContactState == 'ERROR':
                            State = 'Error'
                        else:
                            State = 'Unknown'
                else:
                    State = 'Available'

            logger.info(f'Agent: {AgentName}+ ({Username}) State: {State}')
            if len(AgentName) == 1: logger.warning('Expected first and last name of agent but did not get it in the event.')

            SaveStateToDDB(Username, AgentName, AgentARN, State)
            continue
        if EventType == 'HEART_BEAT':
            # Not sure what to do here yet
            continue
        
        logger.warning(f'Unknown event type: {EventType}')