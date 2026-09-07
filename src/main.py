"""Link Prospecting Tool"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse
import math

from apify import Actor


# --- Constants ---

GOOGLE_SEARCH_SCRAPER_ID = 'nFJndFXA5zjCTuudP'
WEBSITE_CONTENT_CRAWLER_ID = 'aYG0l9s7dbB7j3gbS'
CONTACT_DETAILS_SCRAPER_ID = '9Sk4JJhEma9vBKqrg'




# AI platform config: (input_key, input_value, output_key, text_field, platform_label)
AI_PLATFORMS = {
    'enableChatGpt': {
        'input_key': 'chatGptSearch',
        'input_value': {'enableChatGpt': True},
        'output_key': 'chatGptSearchResult',
        'text_field': 'text',
        'label': 'ChatGPT',
    },
    'enableAiMode': {
        'input_key': 'aiModeSearch',
        'input_value': {'enableAiMode': True},
        'output_key': 'aiModeSearchResult',
        'text_field': 'text',
        'label': 'GoogleAIMode',
    },
    'enableAiOverviews': {
        'input_key': None,  # AI Overviews are included by default; no input flag needed.
        'input_value': None,
        'output_key': 'aiOverview',
        'text_field': 'content',
        'label': 'GoogleAIOverview',
    },
    'enablePerplexity': {
        'input_key': 'perplexitySearch',
        'input_value': {'enablePerplexity': True},
        'output_key': 'perplexitySearchResult',
        'text_field': 'text',
        'label': 'Perplexity',
    },
}


# --- Utility functions ---

# get root domain but keep http: => good to prepare WCC input
def get_root_url(url: str) -> str:
    end = url.find("/", 8)
    if end > 0:
        url =  url[0:end]
    return url

def strip_utm_params(url: str) -> str:
    # Drop the query string and the fragment. AI-search sources often include
    # scroll-to-text fragments ("#:~:text=...") whose unencoded punctuation
    # makes WCC's URL validator reject the whole startUrls list.
    try:
        cut_points = [i for i in (url.find("?"), url.find("#")) if i > 0]
        if cut_points:
            url = url[: min(cut_points)]
        return url
    except Exception:
        return url

# Get domain without the http => good to prepare the contact finder input
def extract_domain(url: str) -> str:
    try:
        return urlparse(url).hostname.replace('www.', '', 1)
    except Exception:
        return ''


def extract_brand_urls(content: str, own_domains: list[str]) -> list[str]:
    if not own_domains:
        return []

    url_regex = re.compile(r'https?://[^\s)\]"\'<>]+', re.IGNORECASE)
    matches = url_regex.findall(content)

    brand_urls: set[str] = set()
    for url in matches:
        domain = extract_domain(url)
        for own in own_domains:
            own_lower = own.lower()
            if domain == own_lower or domain.endswith(f'.{own_lower}'):
                cleaned = re.sub(r'[.,;:!?)]+$', '', url)
                brand_urls.add(cleaned)
                break

    return list(brand_urls)


# Legacy code to detect author without the AI Web Scraper.
def detect_author(content: str) -> dict | None:
    """Detect article author or copywriter from page content.

    Returns {name, linkedin_url} or None.
    """
    name_group = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})'
    patterns = [
        re.compile(r'[Aa]uthor:\s*' + name_group),
        re.compile(r'[Ww]ritten\s+by\s+' + name_group),
        re.compile(r'[Pp]osted\s+by\s+' + name_group),
        re.compile(r'[Ss]hared\s+by\s+' + name_group),
        re.compile(r'[Ww]riter:\s*' + name_group),
        re.compile(r'\b[Bb]y\s+' + name_group),
    ]

    author_name = None
    for pattern in patterns:
        match = pattern.search(content)
        if match:
            author_name = match.group(1).strip()
            break

    linkedin_pattern = re.compile(
        r'https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+/?', re.IGNORECASE
    )
    linkedin_match = linkedin_pattern.search(content)
    linkedin_url = linkedin_match.group(0) if linkedin_match else None

    # Fall back to company LinkedIn URL if no personal URL is found.
    if linkedin_url is None:
        linkedin_pattern = re.compile(
            r'https?://(?:www\.)?linkedin\.com/company/[a-zA-Z0-9_-]+/?', re.IGNORECASE
        )
        linkedin_match = linkedin_pattern.search(content)
        linkedin_url = linkedin_match.group(0) if linkedin_match else None

    return {'name': author_name, 'linkedin_url': linkedin_url}


# --- Main ---

async def main() -> None:
    async with Actor:
        main_dataset = await Actor.open_dataset(alias='mention')
        subResults_dataset = await Actor.open_dataset(alias='subResults')
        Actor.log.info('Default dataset opened.')

        actor_input = await Actor.get_input() or {}

        # Parse input.
        brand = actor_input.get('brand', '').strip()
        queries_raw = actor_input.get('queries', '').strip()

        

        department = actor_input.get('department')
        if len(department) == 0:
            department = ['marketing']

        if not brand:
            raise ValueError('Brand name is required.')
        if not queries_raw:
            raise ValueError('At least one search query is required.')

        own_domains: list[str] = actor_input.get('ownDomains') or []
        competitor_domains: list[str] = actor_input.get('competitorDomains') or []
        max_contacts_per_domain: int = actor_input.get('maxContactsPerDomain', 1)
        SKIP_CONTACT_DOMAINS: list[str] = actor_input.get('ignoreDomains') or []

        enable_flags = {
            'enableChatGpt': actor_input.get('enableChatGpt', True),
            'enableAiMode': actor_input.get('enableAiMode', False),
            'enableAiOverviews': actor_input.get('enableAiOverviews', True),
            'enablePerplexity': actor_input.get('enablePerplexity', False),
        }

        organic_result_count: int = actor_input.get('organicResult', 0)
        search_author_name = actor_input.get('searchAuthorName', False)
        include_mention = actor_input.get('includeMention', False)

        #if not any(enable_flags.values()):
            #raise ValueError('At least one AI platform must be enabled.')

        enable_chat_gpt = actor_input.get('enableChatGpt', True)
        enable_ai_mode = actor_input.get('enableAiMode', True)
        # enableAiOverviews is included by default; no flag needed.
        enable_perplexity = actor_input.get('enablePerplexity', True)
        enableCopilot  = actor_input.get('enableCopilot', True)
        enableGemini  = actor_input.get('enableGemini', True)

        queries = [q.strip() for q in queries_raw.split('\n') if q.strip()]
        if not queries:
            raise ValueError('No valid queries found. Enter one query per line.')

        enabled_names = [
            AI_PLATFORMS[k]['label'] for k, v in enable_flags.items() if v
        ]
        Actor.log.info(
            f'Starting Link Prospecting Tool for brand "{brand}" with {len(queries)} '
            f'queries. Platforms: {", ".join(enabled_names)}'
        )

        excluded_domains = [d.lower() for d in own_domains + competitor_domains]

        # Init variables.
        processed_source_urls = list()
        scraped_contact_domains = list()
        client = Actor.apify_client




        # Step 1: Run Google Search Scraper.
        search_input: dict = {
            'queries': queries_raw,
            'maxPagesPerQuery':  int( math.ceil(organic_result_count / 10))
        
        }

        # Enable requested AI platforms.
        if enable_chat_gpt:
            search_input['chatGptSearch'] = {'enableChatGpt': True}

        if enable_ai_mode:
            search_input['aiModeSearch'] = {'enableAiMode': True}

        if enable_perplexity:
            search_input['perplexitySearch'] = {'enablePerplexity': True}

        if enableGemini:
            search_input['geminiSearch'] = {  "enableGemini": True  }
        if enableCopilot:
            search_input['copilotSearch'] =  {   'enableCopilot': True  }

        try:
            run = await client.actor(GOOGLE_SEARCH_SCRAPER_ID).call(
                run_input=search_input,
                
            )
            dataset = client.dataset(run.default_dataset_id)
            await subResults_dataset.push_data({"actor": "Google Search Result Scraper", "resultUrl": "https://console.apify.com/storage/datasets/"  + run.default_dataset_id})
            items_page = await dataset.list_items()
            search_items = items_page.items

            Actor.log.info(f'Got {len(search_items)} search result(s) from Google Search Scraper.')

            # Init appearance tracking lists.
            perplexity_appearance = list()
            chat_gpt_appearance = list()
            ai_overview_appearance = list()
            organic_result_appearance = list()
            url_per_query = list()
            ai_mode_appearance = list()
            gemini_apperance = list() 
            copilot_apperance = list() 

            # Collect source URLs from all AI platforms and organic results.
            for item in search_items:
                query = item['searchQuery']['term']

                # Parse Perplexity results.
                try:
                    perplexity_result = item['perplexitySearchResult']['citationUrls']
                except Exception:
                    perplexity_result = list()

                for url in perplexity_result:
                    clean_url = strip_utm_params(url)
                    processed_source_urls.append(clean_url)
                    perplexity_appearance.append(clean_url)
                    url_per_query.append({'url': clean_url, 'query': query})

                # Parse ChatGPT results.
                try:
                    chat_gpt_search_result = item['chatGptSearchResult']['sources']
                except Exception:
                    chat_gpt_search_result = list()
                for line in chat_gpt_search_result:
                    try:
                        clean_url = strip_utm_params(line['url'])
                        processed_source_urls.append(clean_url)
                        chat_gpt_appearance.append(clean_url)
                        url_per_query.append({'url': clean_url, 'query': query})
                    except Exception:
                        clean_url = ''

                # Parse organic results.
                try:
                    organic_results = item['organicResults']
                except Exception:
                    organic_results = list()
                for line in organic_results:
                    try:
                        clean_url = strip_utm_params(line['url'])
                        processed_source_urls.append(clean_url)
                        organic_result_appearance.append(clean_url)
                        url_per_query.append({'url': clean_url, 'query': query})
                    except Exception:
                        clean_url = ''

                # Parse AI Overview results.
                try:
                    ai_overview_sources = item['aiOverview']['sources']
                except Exception:
                    ai_overview_sources = list()
                for line in ai_overview_sources:
                    try:
                        clean_url = strip_utm_params(line['url'])
                        processed_source_urls.append(clean_url)
                        ai_overview_appearance.append(clean_url)
                        url_per_query.append({'url': clean_url, 'query': query})
                    except Exception:
                        clean_url = ''
                
                # Parse AImode results.
                try:
                    ai_overview_sources = item['aiModeResult']['sources']
                except Exception:
                    ai_overview_sources = list()
                for line in ai_overview_sources:
                    try:
                        clean_url = strip_utm_params(line['url'])
                        processed_source_urls.append(clean_url)
                        ai_mode_appearance.append(clean_url)
                        url_per_query.append({'url': clean_url, 'query': query})
                    except Exception:
                        clean_url = ''
                # parse gemini results
                try:
                    ai_overview_sources = item['geminiSearchResult']['sources']
                except:
                    ai_overview_sources = list()
                for line in ai_overview_sources:
                    try:
                        clean_url = strip_utm_params(line['url'])
                        processed_source_urls.append(clean_url)
                        gemini_apperance.append(clean_url)
                        url_per_query.append({'url': clean_url, 'query': query})
                    except Exception:
                        clean_url = ''
                
                # parse copilot results
                try:
                    ai_overview_sources = item['copilotSearchResult']['sources']
                except Exception:
                    ai_overview_sources = list()
                for line in ai_overview_sources:
                    try:
                        clean_url = strip_utm_params(line['url'])
                        processed_source_urls.append(clean_url)
                        copilot_apperance.append(clean_url)
                        url_per_query.append({'url': clean_url, 'query': query})
                    except Exception:
                        clean_url = ''      
                    
        except Exception as error:
            Actor.log.error(f'Google Search Scraper run failed: {error}')

        # Deduplicate source URLs.
        processed_source_urls = list(set(processed_source_urls))
        Actor.log.info(f'Got {len(processed_source_urls)} unique source URL(s) from AI search.')

        # Filter out blacklisted, own, and competitor domains.
        filtered_source = list()
        for url in processed_source_urls:
            if not url.startswith(('http://', 'https://')):
                Actor.log.info(f'Skipped non-http(s) source: {url}')
                continue
            domain = extract_domain(url)
            if not domain:
                Actor.log.info(f'Skipped source without a domain: {url}')
                continue
            if domain in SKIP_CONTACT_DOMAINS:
                Actor.log.info(f'Skipped blacklisted source: {url}')
            elif domain in own_domains:
                Actor.log.info(f'Skipped own domain: {url}')
            elif domain in competitor_domains:
                Actor.log.info(f'Skipped competitor domain: {url}')
            else:
                filtered_source.append(url)

        Actor.log.info(f'{len(filtered_source)} source(s) remaining after filtering.')


        # Step 2: Run Website Content Crawler.
        run_input = {
            'maxCrawlDepth': 0,
            'maxRequestRetries': 3,
            'maxPagesPerCrawl': 1,
            'htmlTransformer': 'none',
            'removeElementsCssSelector': '[role="region"]',
        }

        wcc_start_url = [{'url': url} for url in filtered_source]
        run_input['startUrls'] = wcc_start_url

        wcc_items = list()
        if not filtered_source:
            Actor.log.warning('No source URLs available for the Website Content Crawler; skipping step 2.')
        else:
            try:
                wcc_run = await client.actor(WEBSITE_CONTENT_CRAWLER_ID).call(
                    run_input=run_input,
                    memory_mbytes = 4096

                )
                wcc_dataset = client.dataset(wcc_run.default_dataset_id)
                await subResults_dataset.push_data({"actor": "Website Content Crawler", "resultUrl": "https://console.apify.com/storage/datasets/"  + wcc_run.default_dataset_id})
                wcc_items_page = await wcc_dataset.list_items()
                wcc_items = wcc_items_page.items
                Actor.log.info(f'Website Content Crawler returned {len(wcc_items)} page(s).')
            except Exception as error:
                Actor.log.error(f'Website Content Crawler run failed: {error}')


        # Step 3: Process Website Content Crawler results.
        to_enrich = list()
        source_list = list()

        for website in wcc_items:
            current_url = website['url']
            page_content = website['text'] + website['markdown']

            brand_mentioned_in_source = brand.lower() in page_content.lower()
            brand_urls_in_source = extract_brand_urls(page_content, own_domains)

            result = {}
            result['url'] = current_url
            domain = get_root_url(current_url)
            result['domain'] = domain
            result['brand_mentioned_in_source'] = brand_mentioned_in_source
            result['backlink_in_source'] = len(brand_urls_in_source) > 0

            # save data to add to the lead list
            source_list.append({"domain": extract_domain(current_url), "brand_mentioned_in_source": brand_mentioned_in_source, "url": current_url})

            # Map URL to AI platform appearances.
            result['Perplexity_mention'] = current_url in perplexity_appearance
            result['ChatGPT_mention'] = current_url in chat_gpt_appearance
            result['AIOverview_mention'] = current_url in ai_overview_appearance
            result['AIMode'] = current_url in ai_mode_appearance
            result['OrganicResult_mention'] = current_url in organic_result_appearance
            result['Gemini_mention'] = current_url in gemini_apperance
            result['Copilot_mention'] = current_url in copilot_apperance

            result['queries'] = list(set([sub['query'] for sub in url_per_query if sub['url'] == current_url]))

            try:
                await main_dataset.push_data(result)
            except Exception as some_error:
                Actor.log.error(f'Failed to push data to dataset: {some_error}')

            # Add to enrichment list if no backlink exists.
            if not result['backlink_in_source']:
                if include_mention:
                    to_enrich.append(current_url)
                elif not brand_mentioned_in_source:
                    to_enrich.append(current_url)


        # Step 4: Search for contact leads.
        Actor.log.info(f'{len(to_enrich)} source(s) queued for contact enrichment.')

        # Save leads split across named datasets for batch outreach.
        all_leads = await Actor.open_dataset(alias='default')
        batch1 = await Actor.open_dataset(alias='Batch1')
        batch2 = await Actor.open_dataset(alias='Batch2')
        batch3 = await Actor.open_dataset(alias='Batch3')

        

        if len(to_enrich) == 0:
            Actor.log.info("No domain to enrich, you brand is already everywhere")
            await all_leads.push_data({"error": "No url to enrich"})

        else:
            # Deduplicate by root domain.
            to_enrich_domains = list(set([get_root_url(url) for url in to_enrich]))
            start_url = [{'url': url} for url in to_enrich_domains]

            # check if email verification should be enabled
            enableEmailVerification = actor_input.get('enableEmailVerification', True)

            contact_search_input = {
                'leadsEnrichmentDepartments': department,
                'maxDepth': 0,
                'maxRequestsPerStartUrl': 1,
                'maximumLeadsEnrichmentRecords': max_contacts_per_domain,
                'mergeContacts': True,
                'sameDomain': True,
                'startUrls': start_url,
                'verifyLeadsEnrichmentEmails' : enableEmailVerification
            }

            run = await client.actor(CONTACT_DETAILS_SCRAPER_ID).call(
                run_input=contact_search_input
            )

            dataset = client.dataset(run.default_dataset_id)
            await subResults_dataset.push_data({"actor": "Contact Details Scraper", "resultUrl": "https://console.apify.com/storage/datasets/"  + run.default_dataset_id})
            items_page = await dataset.list_items()
            lead_list = items_page.items



            domain_with_leads = list()

            for lead in lead_list:
                try:
                    leads_enrichment = lead['leadsEnrichment']
                    for x, stemp in enumerate(leads_enrichment):
                        stemp['domain'] = lead['domain']
                        domain_with_leads.append(lead['domain'])

                        #get source url data
                        source_url = [sub for sub in source_list if sub['domain'] == stemp['domain']]

                        stemp['source_url'] = source_url
                        # add brand mention data
                        brand_mentioned = False
                        if include_mention:                          
                            for item in source_url:
                                if item["brand_mentioned_in_source"] == True:
                                    brand_mentioned = True
                                    break
                        stemp['brand_mentioned'] = brand_mentioned
    

                        await all_leads.push_data(stemp)
                        if x == 0:
                            await batch1.push_data(stemp)
                        if x == 1:
                            await batch2.push_data(stemp)
                        if x == 2:
                            await batch3.push_data(stemp)
                except Exception as e:
                    Actor.log.error(f'Failed to process lead: {e}')

            # Save list of domains that returned at least one lead.
            domain_with_leads = list(set(domain_with_leads))
            domain_with_leads_dataset = await Actor.open_dataset(alias='domainWithLeads')
            for domain in domain_with_leads:
                await domain_with_leads_dataset.push_data({'domain': domain})


        # Step 5: Search for author data (add-on).
        if search_author_name:
            author_dataset = await Actor.open_dataset(alias='AuthorList')

            if len(to_enrich) == 0:
                Actor.log.info("No domain to enrich, you brand is already everywhere")
                await author_dataset.push_data({"error": "No url to enrich"})
            else:

                # Prepare input for the AI Web Scraper.
                start_urls = [{'url': url} for url in to_enrich]

                ai_run_input = {
                    'prompt': 'Return the blog post name (as postName), author name (as authorName), and publication date (as date).',
                    'startUrls': start_urls,
                }

                run = await client.actor('paOtbjvyUiNsr1Qms').call(
                    run_input=ai_run_input,
                )
                dataset = client.dataset(run.default_dataset_id)
                await subResults_dataset.push_data({"actor": "AI Web Scraper", "resultUrl": "https://console.apify.com/storage/datasets/"  + run.default_dataset_id})
                items_page = await dataset.list_items()
                author_list = items_page.items

                Actor.log.info(f'Author search returned {len(author_list)} result(s).')

            
                for author in author_list:
                    await author_dataset.push_data(author)
