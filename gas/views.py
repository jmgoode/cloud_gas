# views.py
#
# Copyright (C) 2011-2018 Vas Vasiliadis
# University of Chicago
#
# Application logic for the GAS
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import uuid
import time
import json
from datetime import datetime
from datetime import timedelta

import boto3
from botocore.client import Config
from boto3.dynamodb.conditions import Key

from flask import (abort, flash, redirect, render_template,
  request, session, url_for)

from gas import app, db
from decorators import authenticated, is_premium
from auth import get_profile, update_profile


"""Start annotation request
Create the required AWS S3 policy document and render a form for
uploading an annotation input file using the policy document
"""

@app.route('/annotate', methods=['GET'])

@authenticated
def annotate():
    # Open a connection to the S3 service
    s3 = boto3.client('s3',
    region_name=app.config['AWS_REGION_NAME'],
    config=Config(signature_version='s3v4'))

    #set database and table resource
    bucket_name = app.config['AWS_S3_INPUTS_BUCKET']

    #get user profile information
    user_id = session.get('primary_identity')
    profile = get_profile(identity_id=user_id)

    # Generate unique ID to be used as S3 key (name)
    key_name = app.config['AWS_S3_KEY_PREFIX'] + user_id + '/' + str(uuid.uuid4()) + '~${filename}'

    # Redirect to a route that will call the annotator
    redirect_url = str(request.url) + "/job"

    # Define policy conditions
    # NOTE: We also must inlcude "x-amz-security-token" since we're
    # using temporary credentials via instance roles
    encryption = app.config['AWS_S3_ENCRYPTION']
    acl = app.config['AWS_S3_ACL']
    expires_in = app.config['AWS_SIGNED_REQUEST_EXPIRATION']
    fields = {
    "success_action_redirect": redirect_url,
    "x-amz-server-side-encryption": encryption,
    "acl": acl
    }
    conditions = [
    ["starts-with", "$success_action_redirect", redirect_url],
    {"x-amz-server-side-encryption": encryption},
    {"acl": acl}
    ]

    # Generate the presigned POST call
    # source: http://boto3.readthedocs.io/en/latest/reference/services/s3.html#S3.Client.generate_presigned_post
    presigned_post = s3.generate_presigned_post(Bucket=bucket_name,
    Key=key_name, Fields=fields, Conditions=conditions, ExpiresIn=expires_in)

    # Render the upload form which will parse/submit the presigned POST
    return render_template('annotate.html', s3_post=presigned_post)


"""Fires off an annotation job
Accepts the S3 redirect GET request, parses it to extract
required info, saves a job item to the database, and then
publishes a notification for the annotator service.
"""
@app.route('/annotate/job', methods=['GET'])
@authenticated
def create_annotation_job_request():

    #set database and table resource
    dynamodb = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
    ann_table = dynamodb.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])

    #set SNS and Topic resource
    sns = boto3.resource('sns', region_name=app.config['AWS_REGION_NAME'])
    topic = sns.Topic(app.config['AWS_SNS_JOB_REQUEST_TOPIC'])

    #get attributes
    user_id = session.get('primary_identity')
    profile = get_profile(session.get('primary_identity'))
    name = profile.name
    email = profile.email
    institution = profile.institution
    role = profile.role

    # Parse redirect URL query parameters for S3 object info
    bucket_name = request.args.get('bucket')
    key_name = request.args.get('key')

    # Extract the job ID from the S3 key
    split1 = key_name.split('~')
    split2 = split1[0].split('/')

    filename = split1[1]
    user = split2[1]
    job_id = split2[2]

    # Persist job to database
    data = {"job_id": job_id,
        "user_id": user_id,
        "name": name,
        "email" : email,
        "institution": institution,
        "role": role,
        "input_file_name": filename,
        "s3_inputs_bucket": bucket_name,
        "s3_key_input_file": key_name,
        "submit_time": int(time.time()),
        "job_status": 'PENDING'
    }

    #print DB info to console
    app.logger.info(data)

    #put item into database
    # Source: http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Table.put_item
    ann_table.put_item(Item=data)

    #format message into json object
    #source: https://stackoverflow.com/questions/35071549/json-encoding-error-publishing-sns-message-with-boto3
    message = json.dumps({"default":json.dumps(data,ensure_ascii=False)},ensure_ascii=False)

    # Send message to request queue (publishes to topic to which queue subscribes)
    #source: http://boto3.readthedocs.io/en/latest/reference/services/sns.html#SNS.Topic.publish
    response = topic.publish(
        Message=message,
        MessageStructure='json')

    #return confirm template
    return render_template('annotate_confirm.html', job_id=job_id)


"""List all annotations for the user
"""
@app.route('/annotations', methods=['GET'])
@authenticated
def annotations_list():

    #set resources
    dynamodb = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
    ann_table = dynamodb.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])

    # Query database to get list of annotations and pass to template
    user_id = session.get('primary_identity')
    query = ann_table.query(IndexName='user_id_index',
        Select='ALL_ATTRIBUTES',
        KeyConditionExpression=Key('user_id').eq(user_id)
    )

    #list of annotations
    annotations = query['Items']

    #function to turn UTC timestamp to datetime
    #source: https://docs.python.org/3/library/datetime.html#datetime.datetime.fromtimestamp
    def time_format(x):
        formatted_time = datetime.fromtimestamp(int(x))
        return formatted_time

    #pass annotations and time format function
    return render_template('annotations.html', annotations=annotations, format=time_format)


"""Display details of a specific annotation job
"""
@app.route('/annotations/<id>', methods=['GET'])
@authenticated
def annotation_details(id):
    job_id = id
    #set resources
    dynamodb = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
    ann_table = dynamodb.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])

    s3 = boto3.client('s3',
    region_name=app.config['AWS_REGION_NAME'],
    config=Config(signature_version='s3v4'))

    try:

        #query database
        #source: http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Table.get_item
        query = ann_table.get_item(Key={'job_id':job_id})
        item = query['Item']
        status = item['job_status']
        app.logger.info(status)

        #if the status is complete, we can display link to download (if <30 min for free user)
        if status == 'COMPLETE':
            results_bucket = item['s3_results_bucket']
            ann_key = item['s3_key_result_file']
            log_key = item['s3_key_log_file']

            #generate presigned download url
            # http://boto3.readthedocs.io/en/latest/reference/services/s3.html#S3.Client.generate_presigned_url
            url = s3.generate_presigned_url(ClientMethod='get_object',
                Params={
                    'Bucket': results_bucket,
                    'Key': ann_key,
                    }
            )

            #get datetime from timestamp: https://docs.python.org/3/library/datetime.html#datetime.datetime.fromtimestamp
            submit_time = int(item['submit_time'])
            request = datetime.fromtimestamp(submit_time)
            finish_time = int(item['complete_time'])
            complete = datetime.fromtimestamp(finish_time)
            now = datetime.now()

            #function to see if free view is expired
            def is_free_view_expired(complete, now):
                delta = now - complete
                if delta.seconds > 1800:
                    return True
                else:
                    return False


            #function to display expiration time for free download
            def expiration_time(complete):
                expiration_time = complete + timedelta(seconds=1800)
                return expiration_time.strftime('%m-%d-%y %I:%M:%S %p')

            app.logger.info('Expiration time' + expiration_time(complete))
            #pass DB query
            return render_template('annotation_detail.html', item=item, presigned_url=url, is_free_view_expired=is_free_view_expired, expiration_time=expiration_time,request=request, complete=complete, now=now)

        #if its not complete, we do not display download or log view info
        else:
            submit_time = int(item['submit_time'])
            request = datetime.fromtimestamp(submit_time)
            #pass DB query
            return render_template('annotation_detail.html', item=item, request=request)

    #if it doesnt exist, display a none item
    except:
       item = None
       return render_template('annotation_detail.html', item=item) #, presigned_url=url, request=request, complete=complete)




"""Display the log file for an annotation job
"""
@app.route('/annotations/<id>/log', methods=['GET'])
@authenticated
def annotation_log(id):
    job_id = id
    #set resources
    dynamodb = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
    ann_table = dynamodb.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])

    try:
        #query database
        # http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Table.get_item
        query = ann_table.get_item(Key={'job_id':job_id})
        item = query['Item']
        status = item['job_status']

        #if the status is complete, we can view the log and retrieve it from s3
        if status == 'COMPLETE':
            results_bucket = item['s3_results_bucket']
            log_key = item['s3_key_log_file']
            #read log file into string
            # http://boto3.readthedocs.io/en/latest/reference/services/s3.html#S3.Object.get
            s3 = boto3.resource('s3',region_name=app.config['AWS_REGION_NAME'])
            obj = s3.Object(results_bucket, log_key)
            log_file = obj.get()['Body'].read().decode('utf-8')

        else:
            item = None
            log_file = None

    except:
        item = None
        log_file = None

    return render_template('log_file.html', item=item, log_file=log_file)


"""Subscription management handler
"""
import stripe

@app.route('/subscribe', methods=['GET', 'POST'])
@authenticated
def subscribe():

    #if it is a get request, display signup form
    if request.method == 'GET':
        return render_template('subscribe.html')

    #if it is a post request, subscribe user and display confirmation
    elif request.method == 'POST':

        #extract the stripe token
        token = request.form['stripe_token']

        #get profile information
        profile = get_profile(session.get('primary_identity'))
        name = profile.name
        email = profile.email

        #create customer description
        description = 'Customer profile for ' + name

        #set API key
        stripe.api_key = app.config['STRIPE_SECRET_KEY']

        #create customer
        #source: https://stripe.com/docs/api#create_customer
        customer = stripe.Customer.create(
            description=description,
            email=email,
            source=token 
        )

        #pass stripe_id
        stripe_id = customer.id
        app.logger.info('Customer ID: ' + stripe_id)

        #subscribe customer to plan
        #source: https://stripe.com/docs/api#create_subscription
        subscription = stripe.Subscription.create(
            customer=stripe_id,
            items=[{'plan': 'premium_plan'}],
        )

        #update profile
        update_profile(
            identity_id=session['primary_identity'],
            role='premium_user'
        )
        #test to make sure
        profile = get_profile(session.get('primary_identity'))
        app.logger.info('New profile status: ' + profile.role)

        #set Dynamo resource
        dynamodb = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
        ann_table = dynamodb.Table('jgoode_annotations')
        #set glacier resource
        glacier = boto3.client('glacier', region_name='us-east-1')

        # Query database to get list of annotations, indexed on userID
        user_id = session.get('primary_identity')
        query = ann_table.query(IndexName='user_id_index',
            Select='ALL_ATTRIBUTES',
            KeyConditionExpression=Key('user_id').eq(user_id)
        )
        annotations = query['Items']

        #iterate list of user's jobs, update each item to premium status
        for job in annotations:

            #update status to premium
            ann_table.update_item(
                Key={
                    'job_id': job['job_id'],
                },
                UpdateExpression="SET #R = :a",
                ExpressionAttributeNames={'#R':'role'},
                ExpressionAttributeValues={
                    ':a': 'premium_user', #results bucket
                }
            )

            #initiate glacier retrieval for all files
            #http://boto3.readthedocs.io/en/latest/reference/services/glacier.html#Glacier.Client.initiate_job
            #try expedited
            try:
                glacier.initiate_job(
                    vaultName='ucmpcs',
                    jobParameters={
                        'Type': 'archive-retrieval',
                        'ArchiveId': job['results_file_archive_id'],
                        'Tier': 'Expedited',
                        'SNSTopic': 'arn:aws:sns:us-east-1:127134666975:jgoode_glacier_retrieval',
                    }
                )

                # update status to RETRIEVING
                # http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Table.update_item
                ann_table.update_item(
                    Key={
                        'job_id': job_id,
                    },
                    UpdateExpression="set results_file_archive_id = :a, archive_status = :b",
                    ExpressionAttributeValues={
                        ':a': archive_id,
                        ':b': 'RETRIEVING'
                    } #archive ID
                )

            #otherwise try standard speed (3-5 hrs)
            except:
                glacier.initiate_job(
                    vaultName='ucmpcs',
                    jobParameters={
                        'Type': 'archive-retrieval',
                        'ArchiveId': job['results_file_archive_id'],
                        'Tier': 'Standard',
                        'SNSTopic': 'arn:aws:sns:us-east-1:127134666975:jgoode_glacier_retrieval',
                    }
                )

                # update status to RETRIEVING
                ann_table.update_item(
                    Key={
                        'job_id': job_id,
                    },
                    UpdateExpression="set results_file_archive_id = :a, archive_status = :b",
                    ExpressionAttributeValues={
                        ':a': archive_id,
                        ':b': 'RETRIEVING'
                    } #archive ID
                )

        #return template
        return render_template('subscribe_confirm.html', stripe_id=stripe_id)


"""DO NOT CHANGE CODE BELOW THIS LINE
*******************************************************************************
"""

"""Home page
"""
@app.route('/', methods=['GET'])
def home():
  return render_template('home.html')

"""Login page; send user to Globus Auth
"""
@app.route('/login', methods=['GET'])
def login():
  app.logger.info('Login attempted from IP {0}'.format(request.remote_addr))
  # If user requested a specific page, save it to session for redirect after authentication
  if (request.args.get('next')):
    session['next'] = request.args.get('next')
  return redirect(url_for('authcallback'))

"""404 error handler
"""
@app.errorhandler(404)
def page_not_found(e):
  return render_template('error.html',
    title='Page not found', alert_level='warning',
    message="The page you tried to reach does not exist. Please check the URL and try again."), 404

"""403 error handler
"""
@app.errorhandler(403)
def forbidden(e):
  return render_template('error.html',
    title='Not authorized', alert_level='danger',
    message="You are not authorized to access this page. If you think you deserve to be granted access, please contact the supreme leader of the mutating genome revolutionary party."), 403

"""405 error handler
"""
@app.errorhandler(405)
def not_allowed(e):
  return render_template('error.html',
    title='Not allowed', alert_level='warning',
    message="You attempted an operation that's not allowed; get your act together, hacker!"), 405

"""500 error handler
"""
@app.errorhandler(500)
def internal_error(error):
  return render_template('error.html',
    title='Server error', alert_level='danger',
    message="The server encountered an error and could not process your request."), 500

### EOF
