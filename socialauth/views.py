import logging

import urllib
import urllib2
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.contrib.auth.models import UserManager, User
from django.contrib.auth import authenticate, login
from django.http import HttpResponseRedirect, HttpResponseForbidden, HttpResponse
from django.core.urlresolvers import reverse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import logout
from django.utils.translation import ugettext as _
try:
    import json #Works with Python 2.6
except ImportError:
    from django.utils import simplejson as json

from socialauth.models import OpenidProfile, AuthMeta, FacebookUserProfile, TwitterUserProfile, LinkedInUserProfile
from socialauth.forms import EditProfileForm

"""
from socialauth.models import YahooContact, TwitterContact, FacebookContact,\
                            SocialProfile, GmailContact
"""

from openid_consumer.views import begin
from socialauth.lib import oauthtwitter2 as oauthtwitter
from socialauth.lib import oauthyahoo
from socialauth.lib import oauthgoogle
from socialauth.lib.facebook import get_user_info, get_facebook_signature, \
                            get_friends, get_friends_via_fql
from socialauth.lib.linkedin import *

from oauth import oauth
from re import escape
import random
from datetime import datetime
from cgi import parse_qs



def login_page(request):
    return render_to_response('socialauth/login_page.html', context_instance=RequestContext(request))

def facebook_xd_receiver(request):
    return render_to_response('socialauth/xd_reciever.htm')

def linkedin_login(request):
    linkedin = LinkedIn(settings.LINKEDIN_CONSUMER_KEY, settings.LINKEDIN_CONSUMER_SECRET)
    request_token = linkedin.getRequestToken(callback = request.build_absolute_uri(reverse('socialauth_linkedin_login_done')))
    request.session['linkedin_request_token'] = request_token
    signin_url = linkedin.getAuthorizeUrl(request_token)
    return HttpResponseRedirect(signin_url)

def linkedin_login_done(request):
    request_token = request.session.get('linkedin_request_token', None)

    # If there is no request_token for session
    # Means we didn't redirect user to linkedin
    if not request_token:
        # Send them to the login page
        return HttpResponseRedirect(reverse("socialauth_login_page"))

    linkedin = LinkedIn(settings.LINKEDIN_CONSUMER_KEY, settings.LINKEDIN_CONSUMER_SECRET)
    verifier = request.GET.get('oauth_verifier', None)
    access_token = linkedin.getAccessToken(request_token,verifier)
    
    request.session['access_token'] = access_token
    if request.user and request.user.is_authenticated():
        res = authenticate(linkedin_access_token=access_token, user=request.user)
        if res:
            return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=true')
        else:
            return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=false')
    else:
        user = authenticate(linkedin_access_token=access_token)
    
        # if user is authenticated then login user
        if user:
            login(request, user)
        else:
            # We were not able to authenticate user
            # Redirect to login page
            del request.session['access_token']
            del request.session['request_token']
            return HttpResponseRedirect(reverse('socialauth_login_page'))

        # authentication was successful, use is now logged in
        return HttpResponseRedirect(settings.LOGIN_REDIRECT_URL)

def twitter_login(request):
    twitter = oauthtwitter.TwitterOAuthClient(settings.TWITTER_CONSUMER_KEY, settings.TWITTER_CONSUMER_SECRET)
    request_token = twitter.fetch_request_token(callback = request.build_absolute_uri(reverse('socialauth_twitter_login_done')))  
    request.session['request_token'] = request_token.to_string()
    signin_url = twitter.authorize_token_url(request_token)
    return HttpResponseRedirect(signin_url)

def twitter_login_done(request):
    request_token = request.session.get('request_token', None)
    verifier = request.GET.get('oauth_verifier', None)
    denied = request.GET.get('denied', None)
    # If we've been denied, put them back to the signin page
    # They probably meant to sign in with facebook >:D
    if denied:
        return HttpResponseRedirect(reverse("socialauth_login_page"))
    
    # If there is no request_token for session,
    # Means we didn't redirect user to twitter
    if not request_token:
        # Redirect the user to the login page,
        return HttpResponseRedirect(reverse("socialauth_login_page"))
    
    token = oauth.OAuthToken.from_string(request_token)
    
    # If the token from session and token from twitter does not match
    #   means something bad happened to tokens
    if token.key != request.GET.get('oauth_token', 'no-token'):
            del request.session['request_token']
            # Redirect the user to the login page
            return HttpResponseRedirect(reverse("socialauth_login_page"))
    
    twitter = oauthtwitter.TwitterOAuthClient(settings.TWITTER_CONSUMER_KEY, settings.TWITTER_CONSUMER_SECRET)  
    access_token = twitter.fetch_access_token(token, verifier)
    
    request.session['access_token'] = access_token.to_string()
    
    if request.user and request.user.is_authenticated():
        res = authenticate(twitter_access_token=access_token, user=request.user)
        if res:
            return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=true')
        else:
            return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=false')
    else:
        user = authenticate(twitter_access_token=access_token)
    
        # if user is authenticated then login user
        if user:
            login(request, user)
        else:
            # We were not able to authenticate user
            # Redirect to login page
            del request.session['access_token']
            del request.session['request_token']
            return HttpResponseRedirect(reverse('socialauth_login_page'))

        # authentication was successful, use is now logged in
        return HttpResponseRedirect(settings.LOGIN_REDIRECT_URL)

def openid_login(request):
    if 'openid_identifier' in request.GET:
        user_url = request.GET.get('openid_identifier')
        request.session['openid_provider'] = user_url
        return begin(request, user_url = user_url)
    else:
        request.session['openid_provider'] = 'Openid'
        return begin(request)

def gmail_login(request):
    request.session['openid_provider'] = 'Google'
    return begin(request, user_url='https://www.google.com/accounts/o8/id')

def gmail_login_complete(request):
    pass


def yahoo_login(request):
    request.session['openid_provider'] = 'Yahoo'
    return begin(request, user_url='http://yahoo.com/')

def openid_done(request, provider=None):
    """
    When the request reaches here, the user has completed the Openid
    authentication flow. He has authorised us to login via Openid, so
    request.openid is populated.
    After coming here, we want to check if we are seeing this openid first time.
    If we are, we will create a new Django user for this Openid, else login the
    existing openid.
    """
    if not provider:
        provider = request.session.get('openid_provider', '')
    if hasattr(request,'openid') and request.openid:
        #check for already existing associations
        openid_key = str(request.openid)
        if request.user and request.user.is_authenticated():
            res = authenticate(openid_key=openid_key, request=request, provider = provider, user=request.user)
            if res:
                return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=true')
            else:
                return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=false')
        else:
            #authenticate and login
            user = authenticate(openid_key=openid_key, request=request, provider = provider)
            if user:
                login(request, user)
                if 'openid_next' in request.session :
                    openid_next = request.session.get('openid_next')
                    if len(openid_next.strip()) >  0 :
                        return HttpResponseRedirect(openid_next)
                return HttpResponseRedirect(settings.LOGIN_REDIRECT_URL)
            else:
                return HttpResponseRedirect(settings.LOGIN_URL)
    else:
        return HttpResponseRedirect(settings.LOGIN_URL)

def facebook_login(request):
    """
    This is a facebook login page for devices
    that cannot use the FBconnect javascript
    e.g. mobiles, iPhones
    """
    if request.REQUEST.get("device"):
        device = request.REQUEST.get("device")
    else:
        device = "mobile"

    params = {}
    params["api_key"] = settings.FACEBOOK_API_KEY
    params["v"] = "1.0"
    params["next"] = reverse("socialauth_facebook_login_done")[1:] # remove leading slash
    params["canvas"] = "0"
    params["fbconnect"] = "1"
    # Cancel link must be a full URL
    params["cancel"] = request.build_absolute_uri(reverse("socialauth_login_page"))

    if device == "mobile":
        url = "http://m.facebook.com/tos.php?" + urllib.urlencode(params)
    elif device == "touch":
        params["connect_display"] = "touch"
        url = "http://www.facebook.com/login.php?" + urllib.urlencode(params)
    else:
        url = "http://facebook.com/login.php?"+urllib.urlencode(params)

    return HttpResponseRedirect(url)
    
def facebook_login_done(request):
    API_KEY = settings.FACEBOOK_API_KEY

    """
    Facebook connect for mobile doesn't set these cookies
    if API_KEY not in request.COOKIES:
        logging.debug("SOCIALAUTH: Facebook API Key not in Cookies, perhaps cookies are disabled")
        logging.debug("SOCIALAUTH: Here are some cookies: " + str(request.COOKIES))
        return HttpResponseRedirect(reverse('socialauth_login_page'))
    """
    if request.user and request.user.is_authenticated():
        res = authenticate(request=request, user=request.user)
        if res:
            return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=true')
        else:
            del request.COOKIES[API_KEY + '_session_key']
            del request.COOKIES[API_KEY + '_user']
            logging.debug("SOCIALAUTH: Couldn't authenticate user with Django, redirecting to Login page")
            return HttpResponseRedirect(settings.ADD_LOGIN_REDIRECT_URL + '?add_login=false')
    else:
        user = authenticate(request = request)

        if not user:
            del request.COOKIES[API_KEY + '_session_key']
            del request.COOKIES[API_KEY + '_user']
            # TODO: maybe the project has its own login page?
            logging.debug("SOCIALAUTH: Couldn't authenticate user with Django, redirecting to Login page")
            return HttpResponseRedirect(reverse('socialauth_login_page'))

        login(request, user)
        logging.debug("SOCIALAUTH: Successfully logged in with Facebook!")
        return HttpResponseRedirect(settings.LOGIN_REDIRECT_URL)

def openid_login_page(request):
    return render_to_response('openid/index.html', context_instance=RequestContext(request))

@login_required
def signin_complete(request):
    return render_to_response('socialauth/signin_complete.html', context_instance=RequestContext(request))

@login_required
def editprofile(request):
    if request.method == 'POST':
        edit_form = EditProfileForm(user=request.user, data=request.POST)
        if edit_form.is_valid():
            user = edit_form.save()
            try:
                user.authmeta.is_profile_modified = True
                user.authmeta.save()
            except AuthMeta.DoesNotExist:
                pass
            if hasattr(user,'openidprofile_set') and user.openidprofile_set.count():
                openid_profile = user.openidprofile_set.all()[0]
                openid_profile.is_valid_username = True
                openid_profile.save()
            try:
                #If there is a profile. notify that we have set the username
                profile = user.get_profile()
                profile.is_valid_username = True
                profile.save()
            except:
                pass
            request.user.message_set.create(message='Your profile has been updated.')
            return HttpResponseRedirect('.')
    if request.method == 'GET':
        edit_form = EditProfileForm(user = request.user)
        
    payload = {'edit_form':edit_form}
    return render_to_response('socialauth/editprofile.html', payload, RequestContext(request))

def social_logout(request):
    # Todo
    # still need to handle FB cookies, session etc.
    
    # let the openid_consumer app handle openid-related cleanup
    from openid_consumer.views import signout as oid_signout
    oid_signout(request)
    
    # normal logout
    logout_response = logout(request)
    
    if getattr(settings, 'LOGOUT_REDIRECT_URL', None):
        return HttpResponseRedirect(settings.LOGOUT_REDIRECT_URL)
    else:
        return logout_response
