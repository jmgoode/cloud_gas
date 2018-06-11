import boto3, botocore
from botocore.exceptions import ClientError
from datetime import datetime
import time, os, json

# Created a new SQS queue (jgoode_glacier_retrieval) that subscribes to completed jobs topic

#Set resources
#set s3 resource

s3 = boto3.client('s3', region_name='us-east-1')
#set SQS resource
sqs = boto3.resource('sqs', region_name='us-east-1')
queue = sqs.Queue('https://sqs.us-east-1.amazonaws.com/127134666975/jgoode_glacier_retrieval')

#set dynamo resource
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
ann_table = dynamodb.Table('jgoode_annotations')

#set glacier resource
glacier = boto3.client('glacier', region_name='us-east-1')

#continuously poll queue to retreive completed archived jobs

while True: #(continuous polling)
    # poll queue checking each message
    sqs_message = queue.receive_messages(
        MaxNumberOfMessages=1, #extract one message at a time
        WaitTimeSeconds=20, #polling time
    )

    # If message read (checks to see if item was extracted)
    if len(sqs_message) == 1:

        #extract job parameters from the message body as before
        raw_message = json.loads(sqs_message[0].body) #decoded json
        message = json.loads(raw_message['Message']) #decoded json
        retrieval_job_id = message['JobId']

        #get contents of the file
        #http://boto3.readthedocs.io/en/latest/reference/services/glacier.html#Glacier.Client.get_job_output
        response = glacier.get_job_output(vaultName='ucmpcs',jobId=retrieval_job_id)
        print('Retrieval successful. Retrieval JobID =' + retrieval_job_id)

        object = response['body']
        #upload back to S3
        #need to obtain key for this file (put results key in initial archive description)
        ann_key = response['archiveDescription']
        file = ann_key.split('/')[2]
        job_id = file.split('~')[0]

        #upload back to S3
        #http://boto3.readthedocs.io/en/latest/reference/services/s3.html#S3.Client.put_object
        upload = s3.put_object(ACL='private',Body=object.read(),Bucket='gas-results',Key=ann_key)
        print('S3 Upload successful. Location =' + ann_key)

        # set archive's status to Restored
        ann_table.update_item(
            Key={
                'job_id': job_id,
            },
            UpdateExpression="set archive_status = :a",
            ExpressionAttributeValues={
                ':a': 'RESTORED'
            } #archive ID
        )

        #delete the message from the queue
        sqs_message[0].delete()

    #if queue is empty, keep running while loop
    else:
        pass
