/* -*- Mode: C++; tab-width: 2; indent-tabs-mode: nil; c-basic-offset: 4 -*-
 *
 * The contents of this file are subject to the Netscape Public License
 * Version 1.0 (the "NPL"); you may not use this file except in
 * compliance with the NPL.  You may obtain a copy of the NPL at
 * http://www.mozilla.org/NPL/
 *
 * Software distributed under the NPL is distributed on an "AS IS" basis,
 * WITHOUT WARRANTY OF ANY KIND, either express or implied. See the NPL
 * for the specific language governing rights and limitations under the
 * NPL.
 *
 * The Initial Developer of this code under the NPL is Netscape
 * Communications Corporation.  Portions created by Netscape are
 * Copyright (C) 1999 Netscape Communications Corporation.  All Rights
 * Reserved.
 */

#include "nsURILoader.h"
#include "nsIURIContentListener.h"
#include "nsIContentHandler.h"
#include "nsILoadGroup.h"
#include "nsIIOService.h"
#include "nsIServiceManager.h"
#include "nsIStreamListener.h"
#include "nsIChannel.h"


#include "nsVoidArray.h"
#include "nsXPIDLString.h"
#include "nsString.h"

static NS_DEFINE_CID(kIOServiceCID, NS_IOSERVICE_CID);
static NS_DEFINE_CID(kURILoaderCID, NS_URI_LOADER_CID);

/* 
 * The nsDocumentOpenInfo contains the state required when a single document
 * is being opened in order to discover the content type...  Each instance remains alive until its target URL has 
 * been loaded (or aborted).
 *
 */
class nsDocumentOpenInfo : public nsIStreamObserver
{
public:
    nsDocumentOpenInfo();

    nsresult Init(nsIURIContentListener * aContentListener);

    NS_DECL_ISUPPORTS

    nsresult Open(nsIURI *aURL, 
                  const char * aCommand,
                  const char * aWindowTarget,
                  nsIEventSinkGetter * aEventSinkGetter,
                  nsILoadGroup* aLoadGroup, 
                  nsISupports * aContext,
                  nsIURI * aReferringURI);

    // nsIStreamObserver methods:
    NS_DECL_NSISTREAMOBSERVER

	// nsIStreamListener methods:
  // NS_DECL_NSISTREAMLISTENER

protected:
    virtual ~nsDocumentOpenInfo();

protected:
    nsCOMPtr<nsIURIContentListener> m_contentListener;
    nsCOMPtr<nsIChannel> m_channel;
    nsCString m_command;
    nsCString m_windowTarget;
};

NS_IMPL_ISUPPORTS1(nsDocumentOpenInfo, nsIStreamObserver);

nsDocumentOpenInfo::nsDocumentOpenInfo()
{
  NS_INIT_ISUPPORTS();
}

nsDocumentOpenInfo::~nsDocumentOpenInfo()
{
}

nsresult nsDocumentOpenInfo::Init(nsIURIContentListener * aContentListener)
{
  m_contentListener = aContentListener;
  return NS_OK;
}

nsresult nsDocumentOpenInfo::Open(nsIURI * aURI, const char * aCommand, 
                                  const char * aWindowTarget,
                                  nsIEventSinkGetter * aEventSinkGetter,
                                  nsILoadGroup* aLoadGroup, 
                                  nsISupports * aContext,
                                  nsIURI * aReferringURI)
{
   // this method is not complete!!! Eventually, we should first go
  // to the content listener and ask them for a protocol handler...
  // if they don't give us one, we need to go to the registry and get
  // the preferred protocol handler. 

  // But for now, I'm going to let necko do the work for us....

  // store any local state
  m_command = aCommand;
  m_windowTarget = aWindowTarget;

  nsresult rv = NS_OK;
  NS_WITH_SERVICE(nsIIOService, pNetService, kIOServiceCID, &rv);
  if (NS_SUCCEEDED(rv))
  {
    nsCOMPtr<nsIChannel> m_channel;
    rv = pNetService->NewChannelFromURI(aCommand, aURI, aLoadGroup, aEventSinkGetter, aReferringURI, getter_AddRefs(m_channel));
    if (NS_FAILED(rv)) return rv; // uhoh we were unable to get a channel to handle the url!!!
    rv = m_channel->AsyncOpen(this, aContext); 
  }

  return rv;
}

NS_IMETHODIMP nsDocumentOpenInfo::OnStartRequest(nsIChannel * aChannel, nsISupports * aCtxt)
{
  nsresult rv = NS_OK;

  nsXPIDLCString aContentType;
  rv = aChannel->GetContentType(getter_Copies(aContentType));
  if (NS_FAILED(rv)) return rv;

  // go to the uri dispatcher and give them our stuff...
  NS_WITH_SERVICE(nsIURILoader, pURILoader, kURILoaderCID, &rv);
  if (NS_SUCCEEDED(rv))
    rv = pURILoader->DispatchContent(aContentType, m_command, m_windowTarget, 
                                     aChannel, aCtxt, m_contentListener);
  return rv;
}

NS_IMETHODIMP nsDocumentOpenInfo::OnStopRequest(nsIChannel * aChannel, nsISupports *aCtxt, 
                                                nsresult aStatus, const PRUnichar * errorMsg)
{
  // what are we going to do if we get a failure code here...that means the open may have failed
  // and we never discovered the content type....so the uri never really was run!!!! uhoh..

  // this method is incomplete until I figure that stuff out.

  return NS_OK;
}

///////////////////////////////////////////////////////////////////////////////////////////////
// Implementation of nsURILoader
///////////////////////////////////////////////////////////////////////////////////////////////

nsURILoader::nsURILoader()
{
  NS_INIT_ISUPPORTS();
  m_listeners = new nsVoidArray();
}

nsURILoader::~nsURILoader()
{
  if (m_listeners)
    delete m_listeners;
}

NS_IMPL_ISUPPORTS1(nsURILoader, nsIURILoader)

NS_IMETHODIMP nsURILoader::RegisterContentListener(nsIURIContentListener * aContentListener)
{
  nsresult rv = NS_OK;
  if (m_listeners)
    m_listeners->AppendElement(aContentListener);
  else
    rv = NS_ERROR_FAILURE;

  return rv;
} 

NS_IMETHODIMP nsURILoader::UnRegisterContentListener(nsIURIContentListener * aContentListener)
{
  if (m_listeners)
    m_listeners->RemoveElement(aContentListener);
  return NS_OK;
  
}

NS_IMETHODIMP nsURILoader::OpenURI(nsIURI *aURI, 
                                   const char * aCommand, 
                                   const char * aWindowTarget,
                                   nsIEventSinkGetter *aEventSinkGetter, 
                                   nsILoadGroup * aLoadGroup,
                                   nsISupports *aContext,
                                   nsIURIContentListener *aContentListener,
                                   nsIURI *aReferringURI)
{
  return OpenURIVia(aURI, aCommand, aWindowTarget, aEventSinkGetter,
                 aLoadGroup, aContext, aContentListener, aReferringURI, 0 /* ip address */);
}

NS_IMETHODIMP nsURILoader::OpenURIVia(nsIURI *aURI, 
                                   const char * aCommand, 
                                   const char * aWindowTarget,
                                   nsIEventSinkGetter *aEventSinkGetter, 
                                   nsILoadGroup * aLoadGroup,
                                   nsISupports *aContext,
                                   nsIURIContentListener *aContentListener,
                                   nsIURI *aReferringURI,
                                   const PRUint32 aLocalIP)
{
  // we need to create a DocumentOpenInfo object which will go ahead and open the url
  // and discover the content type....

  nsresult rv = NS_OK;
  nsDocumentOpenInfo* loader = nsnull;

  if (!aURI) return NS_ERROR_NULL_POINTER;

  NS_NEWXPCOM(loader, nsDocumentOpenInfo);
  if (!loader) return NS_ERROR_OUT_OF_MEMORY;

  NS_ADDREF(loader);
  loader->Init(aContentListener);    // Extra Info

  // now instruct the loader to go ahead and open the url
  rv = loader->Open(aURI, aCommand, aWindowTarget, aEventSinkGetter, aLoadGroup, aContext, aReferringURI);
  NS_RELEASE(loader);

  return NS_OK;
}

nsresult nsURILoader::DispatchContent(const char * aContentType,
                                      const char * aCommand, 
                                      const char * aWindowTarget,
                                      nsIChannel * aChannel, 
                                      nsISupports * aCtxt, 
                                      nsIURIContentListener * aContentListener)
{
  // okay, now we've discovered the content type. We need to do the following:
  // (1) Give our uri content listener first crack at handling this content type.  
  nsresult rv = NS_OK;

  nsCOMPtr<nsIURIContentListener> listenerToUse = aContentListener;
  // find a content handler that can and will handle the content
  PRBool canHandleContent = PR_FALSE;
  if (listenerToUse)
    listenerToUse->CanHandleContent(aContentType, aCommand, aWindowTarget, &canHandleContent);
  if (!canHandleContent) // if it can't handle the content, scan through the list of registered listeners
  {
     PRInt32 i = 0;
     // keep looping until we are told to abort or we get a content listener back
     for(i = 0; i < m_listeners->Count() && !canHandleContent; i++)
	   {
	      //nsIURIContentListener's aren't refcounted.
		    nsIURIContentListener * listener =(nsIURIContentListener*)m_listeners->ElementAt(i);
        if (listener)
         {
            rv = listener->CanHandleContent(aContentType, aCommand, aWindowTarget, &canHandleContent);
            if (canHandleContent)
              listenerToUse = listener;
         }
    } // for loop
  } // if we can't handle the content


  if (canHandleContent && listenerToUse)
  {
      nsCOMPtr<nsIStreamListener> aContentStreamListener;
      PRBool aAbortProcess = PR_FALSE;
      rv = listenerToUse->DoContent(aContentType, aCommand, aWindowTarget, 
                                    aChannel, getter_AddRefs(aContentStreamListener),
                                    &aAbortProcess);

      // the listener is doing all the work from here...we are done!!!
      if (aAbortProcess) return rv;


      // okay, all registered listeners have had a chance to handle this content...
      // did one of them give us a stream listener back? if so, let's start reading data
      // into it...
      if (aContentStreamListener)
      {
        return aChannel->AsyncRead(0, -1, aCtxt, aContentStreamListener);
      }
  }

  // no registered content listeners to handle this type!!! so go to the register 
  // and get a registered nsIContentHandler for our content type. Hand it off 
  // to them...
  // eventually we want to hit up the category manager so we can allow people to
  // over ride the default content type handlers....for now...i'm skipping that part.

  nsCAutoString handlerProgID (NS_CONTENT_HANDLER_PROGID_PREFIX);
  handlerProgID += aContentType;
  
  nsCOMPtr<nsIContentHandler> aContentHandler;
  rv = nsComponentManager::CreateInstance(handlerProgID, nsnull, NS_GET_IID(nsIContentHandler), getter_AddRefs(aContentHandler));
  if (NS_SUCCEEDED(rv)) // we did indeed have a content handler for this type!! yippee...
    rv = aContentHandler->HandleContent(aContentType, aCommand, aWindowTarget, aChannel);
  return rv;
}

