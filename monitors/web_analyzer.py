"""
WebScraper Module - Analyze websites for quality and localization readiness.

This module provides comprehensive website analysis including:
- AI-powered natural language analysis
- Localization readiness scoring
- Technical stack detection
- Quality assessment metrics
"""

import requests
from bs4 import BeautifulSoup
import time
import re
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, urljoin
from config import Config
from .enhanced_heuristics import analyze_social_multi_region

import os

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


class LocalizationScorer:
    """Calculate localization readiness score for websites."""

    @staticmethod
    def calculate_score(soup: BeautifulSoup, url: str, links: List[Dict], html_content: str) -> Dict[str, Any]:
        """
        Calculate a 0-100 localization readiness score.

        Args:
            soup: BeautifulSoup parsed HTML
            url: Website URL
            links: List of extracted links
            html_content: Raw HTML content

        Returns:
            Dictionary with score and detailed breakdown
        """
        score = 0
        max_score = 100
        details = {}

        # 1. HTML lang attribute (10 points)
        html_tag = soup.find('html')
        if html_tag and html_tag.get('lang'):
            score += 10
            details['html_lang'] = html_tag.get('lang')
        else:
            details['html_lang'] = None

        # 2. Hreflang tags (20 points)
        hreflang_tags = soup.find_all('link', attrs={'hreflang': True})
        if len(hreflang_tags) > 1:  # At least 2 languages
            score += 20
            details['hreflang_count'] = len(hreflang_tags)
        else:
            details['hreflang_count'] = len(hreflang_tags)

        # 3. Language switcher detection (25 points)
        language_switcher_indicators = [
            'language', 'lang', 'locale', 'region', 'country',
            'translate', 'translation', 'idioma', 'langue', 'sprache'
        ]

        has_switcher = False
        for link in links:
            link_text = link.get('text', '').lower()
            link_href = link.get('href', '').lower()
            if any(indicator in link_text or indicator in link_href for indicator in language_switcher_indicators):
                has_switcher = True
                break

        if has_switcher:
            score += 25
            details['language_switcher'] = True
        else:
            details['language_switcher'] = False

        # 4. Translated URL structure (15 points)
        # Check if URLs follow /en/, /fr/, /de/ pattern
        localized_url_pattern = re.compile(r'/(en|fr|de|es|it|pt|ja|zh|ko|ru|ar|nl|sv|no|da|fi|pl|tr|cs|hu|el|he)/[^/]')
        localized_urls = []
        for link in links[:50]:  # Check first 50 links
            href = link.get('href', '')
            if localized_url_pattern.search(href):
                localized_urls.append(href)

        if len(localized_urls) > 3:
            score += 15
            details['localized_url_structure'] = True
            details['localized_url_count'] = len(localized_urls)
        else:
            details['localized_url_structure'] = False
            details['localized_url_count'] = len(localized_urls)

        # 5. i18n JavaScript libraries loaded (15 points)
        i18n_libs = ['i18next', 'react-i18next', 'vue-i18n', 'next-i18next', 'angular-translate', 'intl', 'formatjs']
        scripts = soup.find_all('script', src=True)
        loaded_i18n_libs = []
        for script in scripts:
            src = script.get('src', '')
            for lib in i18n_libs:
                if lib in src.lower():
                    loaded_i18n_libs.append(lib)
                    break

        if loaded_i18n_libs:
            score += 15
            details['i18n_libraries'] = loaded_i18n_libs
        else:
            details['i18n_libraries'] = []

        # 6. Multiple language meta tags (10 points)
        og_locale_tags = soup.find_all('meta', attrs={'property': 'og:locale:alternate'})
        if len(og_locale_tags) > 0:
            score += 10
            details['og_locale_alternate_count'] = len(og_locale_tags)
        else:
            details['og_locale_alternate_count'] = 0

        # 7. Translation management platform detection (5 points)
        tmp_platforms = ['lokalise', 'crowdin', 'phrase', 'transifex', 'weglot', 'smartling', 'pontoon']
        detected_tmp = []
        for platform in tmp_platforms:
            if platform in html_content.lower():
                detected_tmp.append(platform)

        if detected_tmp:
            score += 5
            details['translation_platforms'] = detected_tmp
        else:
            details['translation_platforms'] = []

        return {
            'score': min(score, max_score),
            'max_score': max_score,
            'grade': _get_grade(score),
            'details': details,
            'ready_for_localization': score < 30,  # Low score = opportunity
            'has_partial_localization': 30 <= score < 70,
            'fully_localized': score >= 70
        }


class TechnicalStackDetector:
    """Detect technical stack and frameworks used on websites."""

    @staticmethod
    def detect(soup: BeautifulSoup, html_content: str, response_headers: Dict) -> Dict[str, Any]:
        """
        Detect technical stack from website.

        Args:
            soup: BeautifulSoup parsed HTML
            html_content: Raw HTML content
            response_headers: HTTP response headers

        Returns:
            Dictionary with detected technologies
        """
        stack = {
            'framework': None,
            'frontend_libs': [],
            'i18n_libs': [],
            'cms': None,
            'cdn': None,
            'server': None,
            'analytics': []
        }

        # Frontend frameworks
        if 'next.js' in html_content.lower() or '__NEXT_DATA__' in html_content:
            stack['framework'] = 'Next.js'
        elif 'react' in html_content.lower() and '_reactRootContainer' in html_content:
            stack['framework'] = 'React'
        elif 'vue' in html_content.lower() or 'data-v-' in html_content:
            stack['framework'] = 'Vue.js'
        elif 'angular' in html_content.lower() or 'ng-version' in html_content:
            stack['framework'] = 'Angular'
        elif 'svelte' in html_content.lower():
            stack['framework'] = 'Svelte'

        # Frontend libraries
        scripts = soup.find_all('script', src=True)
        for script in scripts:
            src = script.get('src', '').lower()
            if 'jquery' in src:
                stack['frontend_libs'].append('jQuery')
            if 'bootstrap' in src:
                stack['frontend_libs'].append('Bootstrap')
            if 'tailwind' in src:
                stack['frontend_libs'].append('Tailwind CSS')

        # i18n libraries
        i18n_patterns = {
            'react-i18next': 'react-i18next',
            'next-i18next': 'next-i18next',
            'vue-i18n': 'vue-i18n',
            'i18next': 'i18next',
            'angular-translate': 'angular-translate',
            'formatjs': 'FormatJS',
            '@lingui': 'Lingui'
        }
        for pattern, lib_name in i18n_patterns.items():
            if pattern in html_content.lower():
                stack['i18n_libs'].append(lib_name)

        # CMS detection
        cms_patterns = {
            'wordpress': 'WordPress',
            'wp-content': 'WordPress',
            'drupal': 'Drupal',
            'joomla': 'Joomla',
            'shopify': 'Shopify',
            'wix': 'Wix',
            'squarespace': 'Squarespace',
            'webflow': 'Webflow'
        }
        for pattern, cms_name in cms_patterns.items():
            if pattern in html_content.lower():
                stack['cms'] = cms_name
                break

        # CDN detection from headers
        cdn_header_patterns = {
            'cloudflare': 'Cloudflare',
            'cf-': 'Cloudflare',
            'fastly': 'Fastly',
            'akamai': 'Akamai',
            'amazon': 'Amazon CloudFront'
        }
        for header, value in response_headers.items():
            header_lower = header.lower()
            value_lower = str(value).lower()
            for pattern, cdn_name in cdn_header_patterns.items():
                if pattern in header_lower or pattern in value_lower:
                    stack['cdn'] = cdn_name
                    break

        # Server detection
        server_header = response_headers.get('Server', response_headers.get('server', ''))
        if server_header:
            stack['server'] = server_header

        # Analytics
        analytics_patterns = {
            'google-analytics': 'Google Analytics',
            'gtag': 'Google Analytics',
            'mixpanel': 'Mixpanel',
            'segment': 'Segment',
            'amplitude': 'Amplitude',
            'hotjar': 'Hotjar',
            'plausible': 'Plausible'
        }
        for pattern, analytics_name in analytics_patterns.items():
            if pattern in html_content.lower():
                stack['analytics'].append(analytics_name)

        return stack


class QualityAssessor:
    """Assess website quality metrics."""

    @staticmethod
    def assess(soup: BeautifulSoup, url: str, response_time: float, status_code: int,
               images: List[Dict], links: List[Dict]) -> Dict[str, Any]:
        """
        Assess website quality.

        Args:
            soup: BeautifulSoup parsed HTML
            url: Website URL
            response_time: Response time in seconds
            status_code: HTTP status code
            images: List of images
            links: List of links

        Returns:
            Dictionary with quality metrics
        """
        quality = {
            'performance': {},
            'seo': {},
            'accessibility': {},
            'security': {},
            'mobile': {},
            'overall_score': 0
        }

        # Performance (max 25 points)
        perf_score = 0
        if response_time < 1.0:
            perf_score = 25
        elif response_time < 2.0:
            perf_score = 20
        elif response_time < 3.0:
            perf_score = 15
        elif response_time < 5.0:
            perf_score = 10
        else:
            perf_score = 5

        quality['performance'] = {
            'load_time': round(response_time, 2),
            'score': perf_score,
            'grade': 'Excellent' if perf_score >= 20 else 'Good' if perf_score >= 15 else 'Fair' if perf_score >= 10 else 'Poor'
        }

        # SEO (max 25 points)
        seo_score = 0

        # Title tag (5 points)
        title = soup.find('title')
        if title and len(title.get_text(strip=True)) > 10:
            seo_score += 5

        # Meta description (5 points)
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and len(meta_desc.get('content', '')) > 50:
            seo_score += 5

        # H1 tag (5 points)
        h1 = soup.find('h1')
        if h1:
            seo_score += 5

        # Meta robots (5 points)
        meta_robots = soup.find('meta', attrs={'name': 'robots'})
        if not meta_robots or 'noindex' not in meta_robots.get('content', '').lower():
            seo_score += 5

        # Canonical tag (5 points)
        canonical = soup.find('link', attrs={'rel': 'canonical'})
        if canonical:
            seo_score += 5

        quality['seo'] = {
            'score': seo_score,
            'has_title': bool(title),
            'has_meta_description': bool(meta_desc),
            'has_h1': bool(h1),
            'has_canonical': bool(canonical)
        }

        # Accessibility (max 20 points)
        accessibility_score = 0

        # Images with alt text (10 points)
        images_with_alt = sum(1 for img in images if img.get('alt'))
        total_images = len(images)
        if total_images > 0:
            alt_percentage = (images_with_alt / total_images) * 100
            if alt_percentage >= 90:
                accessibility_score += 10
            elif alt_percentage >= 70:
                accessibility_score += 7
            elif alt_percentage >= 50:
                accessibility_score += 5
            else:
                accessibility_score += 2

        # Lang attribute (5 points)
        html_tag = soup.find('html')
        if html_tag and html_tag.get('lang'):
            accessibility_score += 5

        # Skip links (5 points)
        skip_links = soup.find_all('a', href=re.compile(r'#(main|content|skip)'))
        if skip_links:
            accessibility_score += 5

        quality['accessibility'] = {
            'score': accessibility_score,
            'images_with_alt': images_with_alt,
            'total_images': total_images,
            'alt_coverage': round((images_with_alt / total_images * 100) if total_images > 0 else 0, 1),
            'has_lang_attribute': bool(html_tag and html_tag.get('lang')),
            'has_skip_links': bool(skip_links)
        }

        # Security (max 15 points)
        security_score = 0

        # HTTPS (10 points)
        if url.startswith('https://'):
            security_score += 10

        # Security headers would require checking response headers
        # This is simplified for now
        security_score += 5  # Base score

        quality['security'] = {
            'score': security_score,
            'uses_https': url.startswith('https://'),
            'status_code': status_code
        }

        # Mobile (max 15 points)
        mobile_score = 0

        # Viewport meta tag (15 points)
        viewport = soup.find('meta', attrs={'name': 'viewport'})
        if viewport and 'width=device-width' in viewport.get('content', ''):
            mobile_score += 15

        quality['mobile'] = {
            'score': mobile_score,
            'has_viewport_meta': bool(viewport),
            'viewport_content': viewport.get('content', '') if viewport else None
        }

        # Calculate overall score (0-100)
        overall_score = (
            quality['performance']['score'] +
            quality['seo']['score'] +
            quality['accessibility']['score'] +
            quality['security']['score'] +
            quality['mobile']['score']
        )

        quality['overall_score'] = overall_score
        quality['overall_grade'] = _get_grade(overall_score)

        return quality


class WebAnalyzer:
    """Comprehensive website analyzer with AI-powered insights."""

    def __init__(self):
        """Initialize the WebAnalyzer."""
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        self.timeout = 15

    def fetch_website(self, url: str) -> Dict[str, Any]:
        """
        Fetch website content and extract comprehensive information.

        Args:
            url: The website URL to fetch

        Returns:
            Dictionary containing the website content and metadata

        Raises:
            Exception: If fetching fails
        """
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        try:
            start_time = time.time()
            response = requests.get(url, headers=self.headers, timeout=self.timeout, allow_redirects=True)
            response_time = time.time() - start_time
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.text, 'lxml')
            html_content = response.text

            # Remove script and style elements for clean text
            soup_clean = BeautifulSoup(html_content, 'lxml')
            for script in soup_clean(['script', 'style', 'noscript']):
                script.decompose()

            # Extract metadata
            title = soup.find('title')
            title_text = title.get_text(strip=True) if title else ''

            # Get meta description
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            description = meta_desc.get('content', '') if meta_desc else ''

            # Get all text content
            text_content = soup_clean.get_text(separator='\n', strip=True)

            # Clean up excessive whitespace
            lines = (line.strip() for line in text_content.splitlines())
            text_content = '\n'.join(line for line in lines if line)

            # Extract links
            links = []
            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                link_text = link.get_text(strip=True)
                if href and link_text:
                    links.append({'href': href, 'text': link_text})

            # Extract images
            images = []
            for img in soup.find_all('img'):
                src = img.get('src', img.get('data-src', ''))
                alt = img.get('alt', '')
                images.append({'src': src, 'alt': alt})

            # Extract meta tags for language/i18n detection
            lang_tags = []
            html_tag = soup.find('html')
            if html_tag and html_tag.get('lang'):
                lang_tags.append(html_tag.get('lang'))

            # Look for hreflang tags
            hreflang_tags = []
            for link in soup.find_all('link', attrs={'hreflang': True}):
                hreflang_tags.append({
                    'hreflang': link.get('hreflang'),
                    'href': link.get('href', '')
                })

            # Perform comprehensive analysis
            localization_score = LocalizationScorer.calculate_score(soup, url, links, html_content)
            tech_stack = TechnicalStackDetector.detect(soup, html_content, dict(response.headers))
            quality_metrics = QualityAssessor.assess(
                soup, url, response_time, response.status_code, images, links
            )

            # Build preliminary website data for social analysis
            preliminary_data = {
                'links': links[:100],
                'hreflang_tags': hreflang_tags,
                'localization_score': localization_score,
            }

            # Analyze social multi-region signals (Heuristic #8)
            social_multi_region = analyze_social_multi_region(preliminary_data)

            return {
                'url': response.url,  # Final URL after redirects
                'status_code': response.status_code,
                'response_time': round(response_time, 2),
                'title': title_text,
                'description': description,
                'text_content': text_content[:50000],  # Limit to 50k chars
                'links': links[:100],  # Limit to first 100 links
                'images': images[:50],  # Limit to first 50 images
                'lang_tags': lang_tags,
                'hreflang_tags': hreflang_tags,
                'content_length': len(text_content),
                'link_count': len(links),
                'image_count': len(images),
                'localization_score': localization_score,
                'tech_stack': tech_stack,
                'quality_metrics': quality_metrics,
                'social_multi_region': social_multi_region
            }

        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to fetch website: {str(e)}")

    def analyze_with_ai(self, website_data: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """
        Analyze website content using AI based on a natural language prompt.
        Uses GPT-5 mini (primary) with Gemini 3.1 Pro fallback.

        Args:
            website_data: Dictionary containing website content and metadata
            prompt: Natural language prompt describing what to analyze

        Returns:
            Dictionary containing analysis results

        Raises:
            Exception: If AI analysis fails
        """
        analysis_prompt = self._build_analysis_prompt(website_data, prompt)

        api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
        base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
        if OPENAI_AVAILABLE and api_key and base_url:
            try:
                client = OpenAI(api_key=api_key, base_url=base_url)
                response = client.chat.completions.create(
                    model="gpt-5-mini",
                    messages=[
                        {"role": "system", "content": "You are a website analysis expert. Provide detailed analysis based on the website data provided."},
                        {"role": "user", "content": analysis_prompt}
                    ],
                    max_completion_tokens=4096
                )
                analysis_text = response.choices[0].message.content

                return {
                    'success': True,
                    'analysis': analysis_text,
                    'metadata': {
                        'url': website_data.get('url'),
                        'title': website_data.get('title'),
                        'content_length': website_data.get('content_length'),
                        'link_count': website_data.get('link_count'),
                        'image_count': website_data.get('image_count'),
                        'model': 'gpt-5-mini',
                    }
                }
            except Exception as e:
                print(f"[WEB_ANALYZER] GPT-5 mini error: {e}, falling back to Gemini...")

        if GENAI_AVAILABLE and Config.GEMINI_API_KEY:
            try:
                client = genai.Client(api_key=Config.GEMINI_API_KEY)
                response = client.models.generate_content(
                    model=Config.GEMINI_MODEL,
                    contents=analysis_prompt
                )
                analysis_text = response.text

                return {
                    'success': True,
                    'analysis': analysis_text,
                    'metadata': {
                        'url': website_data.get('url'),
                        'title': website_data.get('title'),
                        'content_length': website_data.get('content_length'),
                        'link_count': website_data.get('link_count'),
                        'image_count': website_data.get('image_count'),
                        'model': Config.GEMINI_MODEL,
                    }
                }
            except Exception as e:
                raise Exception(f"AI analysis failed: {str(e)}")

        raise Exception("No AI provider available. Please configure OpenAI or Gemini API keys.")

    def _build_analysis_prompt(self, website_data: Dict[str, Any], user_prompt: str) -> str:
        """
        Build the prompt for AI analysis.

        Args:
            website_data: Dictionary containing website content
            user_prompt: User's natural language prompt

        Returns:
            Formatted prompt string
        """
        url = website_data.get('url', 'Unknown')
        title = website_data.get('title', 'Unknown')
        description = website_data.get('description', '')
        text_content = website_data.get('text_content', '')
        links = website_data.get('links', [])
        images = website_data.get('images', [])
        lang_tags = website_data.get('lang_tags', [])
        hreflang_tags = website_data.get('hreflang_tags', [])
        localization_score = website_data.get('localization_score', {})
        tech_stack = website_data.get('tech_stack', {})
        quality_metrics = website_data.get('quality_metrics', {})
        social_multi_region = website_data.get('social_multi_region', {})

        # Build a summary of available data
        data_summary = f"""
Website URL: {url}
Page Title: {title}
Meta Description: {description}
Language Tags: {', '.join(lang_tags) if lang_tags else 'None'}
Hreflang Tags: {len(hreflang_tags)} found
Links Found: {len(links)}
Images Found: {len(images)}

=== LOCALIZATION READINESS ===
Score: {localization_score.get('score', 0)}/100 ({localization_score.get('grade', 'N/A')})
Ready for Localization: {localization_score.get('ready_for_localization', False)}
Language Switcher: {localization_score.get('details', {}).get('language_switcher', False)}
i18n Libraries: {', '.join(localization_score.get('details', {}).get('i18n_libraries', [])) or 'None'}

=== TECHNICAL STACK ===
Framework: {tech_stack.get('framework', 'Unknown')}
i18n Libraries: {', '.join(tech_stack.get('i18n_libs', [])) or 'None'}
CMS: {tech_stack.get('cms', 'None')}
CDN: {tech_stack.get('cdn', 'Unknown')}

=== QUALITY METRICS ===
Overall Score: {quality_metrics.get('overall_score', 0)}/100 ({quality_metrics.get('overall_grade', 'N/A')})
Performance: {quality_metrics.get('performance', {}).get('grade', 'N/A')} ({quality_metrics.get('performance', {}).get('load_time', 0)}s)
Mobile-Ready: {quality_metrics.get('mobile', {}).get('has_viewport_meta', False)}
HTTPS: {quality_metrics.get('security', {}).get('uses_https', False)}

=== SOCIAL & MULTI-REGION SIGNALS ===
Has Multi-Region Social: {social_multi_region.get('has_multi_region_social', False)}
OG Locale Count: {social_multi_region.get('og_locale_count', 0)}
Regional Social Handles: {len(social_multi_region.get('regional_handles', []))} found

=== PAGE CONTENT (First 5000 chars) ===
{text_content[:5000]}

=== SAMPLE LINKS (first 20) ===
"""
        for i, link in enumerate(links[:20]):
            data_summary += f"{i+1}. {link['text']} -> {link['href']}\n"

        if hreflang_tags:
            data_summary += "\n=== HREFLANG TAGS ===\n"
            for tag in hreflang_tags[:10]:
                data_summary += f"- {tag['hreflang']}: {tag['href']}\n"

        # Build the final prompt
        prompt = f"""You are a website analysis assistant specializing in localization readiness and quality assessment. Your task is to analyze website content and extract information based on the user's request.

USER REQUEST:
{user_prompt}

WEBSITE DATA:
{data_summary}

INSTRUCTIONS:
1. Carefully analyze the website content provided above
2. Extract or identify the information requested by the user
3. Pay special attention to localization signals and quality issues
4. Provide a clear, structured response
5. If the requested information is not found, say so explicitly
6. Be concise but thorough
7. Use markdown formatting for better readability (bullet points, headers, etc.)
8. For localization analysis, focus on:
   - Current localization state (score and readiness)
   - Gaps and opportunities
   - Technical implementation recommendations
   - Competitive positioning

Please provide your analysis now:
"""
        return prompt

    def analyze_url(self, url: str, prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        Main method to analyze a URL with optional natural language prompt.

        Args:
            url: Website URL to analyze
            prompt: Optional natural language prompt (if None, returns technical analysis only)

        Returns:
            Dictionary containing analysis results and metadata
        """
        start_time = time.time()

        try:
            # Fetch website content
            website_data = self.fetch_website(url)

            # If prompt provided, also do AI analysis
            if prompt:
                ai_result = self.analyze_with_ai(website_data, prompt)
                analysis_time = round(time.time() - start_time, 2)
                ai_result['metadata']['analysis_time'] = f"{analysis_time}s"

                # Add technical scores to AI result
                ai_result['localization_score'] = website_data['localization_score']
                ai_result['tech_stack'] = website_data['tech_stack']
                ai_result['quality_metrics'] = website_data['quality_metrics']
                ai_result['social_multi_region'] = website_data.get('social_multi_region', {})

                return ai_result
            else:
                # Return technical analysis only
                analysis_time = round(time.time() - start_time, 2)
                return {
                    'success': True,
                    'url': website_data['url'],
                    'title': website_data['title'],
                    'localization_score': website_data['localization_score'],
                    'tech_stack': website_data['tech_stack'],
                    'quality_metrics': website_data['quality_metrics'],
                    'social_multi_region': website_data.get('social_multi_region', {}),
                    'metadata': {
                        'url': website_data['url'],
                        'title': website_data['title'],
                        'analysis_time': f"{analysis_time}s"
                    }
                }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'metadata': {
                    'url': url,
                    'analysis_time': f"{round(time.time() - start_time, 2)}s"
                }
            }


def _get_grade(score: float) -> str:
    """Convert numerical score to letter grade."""
    if score >= 90:
        return 'A+'
    elif score >= 80:
        return 'A'
    elif score >= 70:
        return 'B'
    elif score >= 60:
        return 'C'
    elif score >= 50:
        return 'D'
    else:
        return 'F'


# Convenience function for direct use
def analyze_website(url: str, prompt: str) -> Dict[str, Any]:
    """
    Analyze a website using a natural language prompt.

    Args:
        url: Website URL to analyze
        prompt: Natural language prompt describing what to analyze

    Returns:
        Dictionary containing analysis results
    """
    analyzer = WebAnalyzer()
    return analyzer.analyze_url(url, prompt)


def analyze_website_technical(url: str) -> Dict[str, Any]:
    """
    Analyze a website for technical metrics only (no AI prompt).

    Args:
        url: Website URL to analyze

    Returns:
        Dictionary containing technical analysis results
    """
    analyzer = WebAnalyzer()
    return analyzer.analyze_url(url, prompt=None)
