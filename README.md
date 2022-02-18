## AWS Serverless Connect Wallboard

## Introduction
`aws-serverless-connect-wallboard` provides a way to build near real-time dashboards for your [Amazon Connect](https://aws.amazon.com/connect) contact center. It was introduced [in this blog post](https://aws.amazon.com/blogs/contact-center/building-a-serverless-contact-center-wallboard-for-amazon-connect/) which has further details about architecture and initial setup. These instructions focus more on how to use the software to create wallboards/dashboards.

First, you will need an Amazon Connect instance up and running. If you wish to track state of agents on your wallboard then you will need to [configure an agent event stream](https://docs.aws.amazon.com/connect/latest/adminguide/agent-event-streams.html) - make note of the ARN.

Next, you will need to deploy the [CloudFormation template](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-cfn.yaml) which will build the Lambda functions, DynamoDB table and API Gateway components for you.

The DynamoDB table holds data that will be displayed on the wallboard (this data is refreshed periodically - see below for more information) and the configuration for the wallboard. You can edit the DynamoDB table directly to change the look of your wallboard but it's probably easier to edit a YAML definition file as described below. You can have multiple wallboard definitions contained in a single DynamoDB table. You can also collect historical and real-time data from multiple Connect instances. If you wish to collect agent events from multiple Connect instances you will need to configure Kinesis to deliver the events to Lambda manually (which will be processed by the [agent event handler](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/process-agent-event.py)).

### Wallboard Configuration
Use the [wallboard import utility](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-import.py) to import a YAML definition file for each wallboard into DynamoDB.

Each definition file will use the following format. Note that many parameters are optional - mandatory ones are marked.
```yaml
WallboardTemplateFormatVersion: 1
Description: <description of this wallboard - for humans only>
Identifier: <Mandatory: unique indentifier for this wallboard>

Defaults:
  TextColor: <default HTML color for text>
  BackgroundColor: <default HTML background for each cell>
  TextSize: <default text size>
  Font: <default font>
  WarningBackgroundColor: <color for cells in "warning" state - default is yellow>
  AlertBackgroundColor: <color for cells in "alert" state - default is red>

Sources:
  - Source: <Mandatory: (local wallboard) name of data source>
    Description: <human readable description>
    Reference: <Mandatory: reference to data source in Connect - see below>

Thresholds:
  - Threshold: <Mandatory: unique name of threshold>
    Reference: <Mandatory: name of source created in Sources section - the metric to track>
    WarnBelow: <Either WarnBelow or WarnAbove: value for warning (yellow) when metric is below this value>
    AlertBelow: <Either AlertBelow or AlertAbove: value for alert (red) when metric is below this value>
  - Threshold: <Mandatory: unique name of threshold>
    Reference: <Mandatory: name of source created in Sources section - the metric to track>
    WarnAbove: <Either WarnBelow or WarnAbove: value for warning (yellow) when metric is below this value>
    AlertAbove: <Either AlertBelow or AlertAbove: value for alert (red) when metric is below this value>

Calculations:
  - Calculation: <Mandatory: unique name of calculation>
    Formula: <Mandatory: formula for this calcuation - see below>

AgentStates:
  - State: <Mandatory: state name>
    Color: <Mandatory: color for this state>

Rows:
  - Row: <Mandatory: row number for cells following>
    Cells:
    - Cell: <Mandantory: cell number>
      Text: <optional static text to go in cell>
      TextColor: <HTML color for text in this cell>
      BackgroundColor: <HTML background color for this cell>
      TextSize: <font size for this cell>
      Reference: <name of data source or calculation result to put in this cell>
      ThresholdReference: <name of threshold to apply to this cell>
      Rows: <number of rows to span this cell across - default=1>
      Cells: <number of columns to span this cell across - default=1>
```

### References
When specifying references to data coming from Connect the format for each reference is as follows:
```yaml
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy:AGENTS_AVAILABLE
```

`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` is taken from the Connect instance ARN. For example, if an instance has an ARN of `arn:aws:connect:us-east-1:111122223333:instance/12345678-1234-1234-1234-123456789012` then you want to use `12345678-1234-1234-1234-123456789012` as the first part of the reference.

`yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy` is taken from the Connect queue ARN. For example, if a queue has an ARN of `arn:aws:connect:us-east-1:111122223333:instance/12345678-1234-1234-1234-123456789012/queue/87654321-4321-4321-4321-210987654321` then you want to use `87654321-4321-4321-4321-210987654321` as the second part of the reference.

Finally, you need to specify the metric that you wish to reference. There are many metrics available and the wallboard will retrieve both real-time and historical metrics for you without you needing to specify which API to use. See the [GetCurrentMetricData API documentation](https://docs.aws.amazon.com/connect/latest/APIReference/API_GetCurrentMetricData.html) for real-time metrics and the [GetMetric API documentation](https://docs.aws.amazon.com/connect/latest/APIReference/API_GetMetricData.html) for historical metrics. Examples, are `AGENTS_AVAILABLE` and `CONTACTS_ABANDONED`.

In a cell, you specify which data you wish to display by using the `Reference` tag. The data can be a direct reference (i.e. data that is being drawn from Connect directly); it can be the result of a calculation (several metrics that have been somehow modified - see below); or it can be the name of an agent (see below).

#### Special note about SERVICE_LEVEL
Thanks to `eaagastr` for pointing this out.

SERVICE_LEVEL is an historical metric that requires an additional parameter: Threshold. This is because the metric is determining what the service level is of a queue and therefore needs the number of seconds that it should evaluate the service level over.

For the time being, I've put a small hack into the code so that it doesn't throw an error when SERVICE_LEVEL is requested as a metric. At the top of `get-historical-metrics.py` you'll see a variable which is `ServiceLevelThreshold` and it is set to 60 (seconds). This is static across all queues - you can change this value (between 1 and 604800 inclusive) but you can't set it individually per queue.

In future, this might change - so that you can specify a different threshold per queue. If this is of interest, send me a message.

### Calculations
Calculations allow you to take metrics and perform simple mathematical operations on them. For example, you may have three queues and wish to display the total number of callers for all three queues. To do this, you could use the following snippet:
```yaml
Sources:
  - Source: Queue1Waiting
    Description: Callers waiting in Queue 1
    Reference: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-210987654321:CONTACTS_IN_QUEUE
  - Source: Queue2Waiting
    Description: Callers waiting in Queue 2
    Source: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-543210987544:CONTACTS_IN_QUEUE
  - Source: Queue3Waiting
    Description: Callers waiting in Queue 3
    Reference: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-568295214776:CONTACTS_IN_QUEUE
    
Calculations:
  - Calculation: TotalCallersWaiting
    Formula: Queue1Waiting+Queue2Waiting+Queue3Waiting

Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Text: Queue 1
      Reference: Queue1Waiting
    - Cell: 2
      Text: Queue 2
      Reference: Queue2Waiting
    - Cell: 3
      Text: Queue 3
      Reference: Queue3Waiting
    - Cell: 4
      Text: Total
      Reference: TotalCallersWaiting
```

### Thresholds
Setting thresholds lets you change the background colour of a cell based on the value in that cell or in another cell. You can use this to highlight when (for example) to warn you before a SLA is breached (say, when the maximum waiting time is over two minutes) by setting a threshold at one minute using the `WarnAbove` value and another threshold at two miuntes to show the breach using the `AlertAbove` value.

```yaml
Sources:
  - Source: LongestWaiting
    Description: Longest waiting caller
    Reference: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-210987654321:OLDEST_CONTACT_AGE

Thresholds:
  - Threshold: LongestWaitingWarning
    Reference: LongestWaiting
    WarnAbove: 60
    AlertAvboe: 120

Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Text: Longest waiting
      Reference: LongestWaiting
      ThresholdReference: LongestWaitingWarning
```
You can apply the threshold reference to any other cells even if they do not contain the data that is causing the breach of threshold. That way, you could turn a whole row or column yellow or red (the default colours) to highlight a threshold breach. Thresholds may also reference the output of calculations rather than raw data from Connect.

### Agent states
Make sure that you define colours for each agent state that has been created in Connect. There are no default colours in the wallboard for each state so if a state is detected that doesn't have a colour, the default background colour applies.

To show an agent state in a cell you do not need to create a reference, the login name of the agent is all that is required:
```yaml
AgentStates:
  - State: Available
    Color: Green
  - State: Offline
    Color: Red
  - State: Work
    Color: Orange
  - State: Lunch
    Color: Yellow

Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Description: Agent state for Alice
      Text: Alice
      Reference: alice
    - Cell: 2
      Description: Agent state for Bob
      Text: Bob
      Reference: bob
    - Cell: 3
      Description: Agent state for Carlos
      Text: Carlos
      Reference: carlos
  - Row: 2
    Cells:
    - Cell: 1
      Description: Agent state for Dave
      Text: Dave
      Reference: dave
    - Cell: 2
      Description: Agent state for Erin
      Text: Erin
      Reference: erin
    - Cell: 3
      Description: Agent state for Eve
      Text: Eve
      Reference: eve
```
In this snippet we display the name of the agent (using the `Text` tag) but the `Reference` points to the login name for that agent. The wallboard will automatically update each cell with the appropriate colour based on each agent's current state in Connect.

In a large contact centre, you may not want to have a static list of agents. You may have different agents on different shifts and that would require you to update the wallboard configuration at shift change. Instead, there are two meta values you can use to display a dynamic list of names.
```yaml
Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Description: Agent state
      Reference: =allagents
    - Cell: 2
      Description: Agent state
      Reference: =allagents
    - Cell: 3
      Text: Carlos
      Reference: =allagents
```
This takes the list of agents from Connect and displays them without you needing to know the names in advanced. Make sure that the `First name` and `Last name` attributes for the agent are filled in as these are used in place of the `Text` tag to display the name of the agent. If you have more agents than cells available then additional agents are not displayed. If you have less agents than cells then the cells are left blank.

It may not be useful to display the state of agents who are not currently logged into the system.
```yaml
Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Description: Agent state
      Reference: =activeagents
    - Cell: 2
      Description: Agent state
      Reference: =activeagents
    - Cell: 3
      Text: Carlos
      Reference: =activeagents
```
Here the cells only contain details of agents who are not in a `Logout` state.

### Configuring the Wallboard
Once you have your YAML definition file you need to import it into the DynamoDB table. To do this you'll need the [import utility](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-import.py):
```sh
./wallboard-import.py <definition file>
```
Once imported you can call the API Gateway endpoint that the CloudFormation template configured for you. You can find this in the `Outputs` section of the CloudFormation stack.
```
curl https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/wallboard?Wallboard=standard
```
This example retrieves the wallboard called `standard` which is the name given to it by the `Identifier` tag in the definition file. You can have multiple definitions coexisting in the wallboard system as long as they have unique identifiers. This allows you to have a single set of data that is displayed differently in many locations. For example, you might have an overarching wallboard shown on a large display and also have less complex wallboards that show a subset of the data on agent desktops in a browser.

To display the wallboard, you'll need to write a small piece of Javascript that embeds the wallboard table returned by API Gateway into a web page. Check out [this example page](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-example.html) in this repo for a starting point. Note that you can use CSS to make additional changes to the appearance of the wallboard.

### Wallboard Tuning
You may wish to tune specific events in the wallboard system.

Historical metrics are retrieved every minute. This is triggered by CloudWatch Events and can be changed by modifying the `Connect-Wallboard-Historical-Collection` rule. You can also modify the [CloudFormation template](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-cfn.yaml) before deployment.

The wallboard configuration is checked every 300 seconds (five minutes) by default. This means that when you update an existing wallboard configuration it may take up to five minutes for the changes to be visible. This can be changed by adding an environment variable called `ConfigTimeout` for the `Connect-Wallboard-Render` and `Connect-Wallboard-Historical-Metrics` Lambda functions and making the value the number of seconds the function should wait before checking for any updated configuration.

## License Summary

This sample code is made available under the MIT-0 license. See the LICENSE file.
