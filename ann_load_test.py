import os, json, uuid, sys
import time
from datetime import datetime
from datetime import timedelta

import boto3, botocore
from botocore.client import Config
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key


#set SNS and Topic resource
sns = boto3.resource('sns', region_name='us-east-1')
topic = sns.Topic('arn:aws:sns:us-east-1:127134666975:jgoode_job_requests')
s3 = boto3.resource('s3', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
ann_table = dynamodb.Table('jgoode_annotations')

requests = int(sys.argv[1])

#send a series of messages to the jgoode_job_requests queue
for i in range(0,requests):
    #make a job_id
    job_id = str(uuid.uuid4())
    input_key = 'jgoode/ann_load_test/' + job_id + '~test.vcf'

    #put object in s3
    input_data = open('anntools/data/test.vcf', 'rb')
    s3.Bucket('gas-inputs').put_object(Key=input_key, Body=input_data)

    data = {"job_id": job_id,
        "user_id": 'ann_load_test',
        "name": 'Jason Goode',
        "email" : 'jgoode@uchicago.edu',
        "institution": 'CS',
        "role": 'premium_user', #can switch this out if you want
        "input_file_name": 'test.vcf',
        "s3_inputs_bucket": 'gas-inputs',
        "s3_key_input_file": input_key,
        "submit_time": int(time.time()),
        "job_status": 'PENDING'
    }

    ann_table.put_item(Item=data)

    # publish to results topic
    #https://stackoverflow.com/questions/35071549/json-encoding-error-publishing-sns-message-with-boto3
    message = json.dumps({"default":json.dumps(data,ensure_ascii=False)},ensure_ascii=False)

    # Send message to request queue (publishes to topic to which queue subscribes)
    #source: http://boto3.readthedocs.io/en/latest/reference/services/sns.html#SNS.Topic.publish
    response = topic.publish(
        Message=message,
        MessageStructure='json')
