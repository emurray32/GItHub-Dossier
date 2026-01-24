"""
WebScraper Module - Analyze websites using natural language prompts.

This module fetches website content and uses AI to extract information
based on user-provided natural language prompts.
"""

import requests
from bs4 import BeautifulSoup
import time
from typing import Dict, Any, Optional
from config import Config

try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


class WebAnalyzer:
    """Analyze websites using AI-powered natural language prompts."""

    def __init__(self):
        """Initialize the WebAnalyzer."""
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.timeout = 15

    def fetch_website(self, url: str) -> Dict[str, Any]:
        """
        Fetch website content and extract basic information.

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
            response = requests.get(url, headers=self.headers, timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.text, 'lxml')

            # Remove script and style elements
            for script in soup(['script', 'style', 'noscript']):
                script.decompose()

            # Extract metadata
            title = soup.find('title')
            title_text = title.get_text(strip=True) if title else ''

            # Get meta description
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            description = meta_desc.get('content', '') if meta_desc else ''

            # Get all text content
            text_content = soup.get_text(separator='\n', strip=True)

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
            for img in soup.find_all('img', src=True):
                src = img.get('src', '')
                alt = img.get('alt', '')
                if src:
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

            return {
                'url': response.url,  # Final URL after redirects
                'status_code': response.status_code,
                'title': title_text,
                'description': description,
                'text_content': text_content[:50000],  # Limit to 50k chars to avoid token limits
                'links': links[:100],  # Limit to first 100 links
                'images': images[:50],  # Limit to first 50 images
                'lang_tags': lang_tags,
                'hreflang_tags': hreflang_tags,
                'content_length': len(text_content),
                'link_count': len(links),
                'image_count': len(images),
            }

        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to fetch website: {str(e)}")

    def analyze_with_ai(self, website_data: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """
        Analyze website content using AI based on a natural language prompt.

        Args:
            website_data: Dictionary containing website content and metadata
            prompt: Natural language prompt describing what to analyze

        Returns:
            Dictionary containing analysis results

        Raises:
            Exception: If AI analysis fails
        """
        if not GENAI_AVAILABLE:
            raise Exception("Google Generative AI is not available. Please install google-generativeai.")

        if not Config.GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY not configured. Please set the GOOGLE_API_KEY or GEMINI_API_KEY environment variable.")

        # Build the analysis prompt
        analysis_prompt = self._build_analysis_prompt(website_data, prompt)

        try:
            client = genai.Client(api_key=Config.GEMINI_API_KEY)

            response = client.models.generate_content(
                model=Config.GEMINI_MODEL,
                contents=analysis_prompt
            )

            # Extract the response text
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

        # Build a summary of available data
        data_summary = f"""
Website URL: {url}
Page Title: {title}
Meta Description: {description}
Language Tags: {', '.join(lang_tags) if lang_tags else 'None'}
Hreflang Tags: {len(hreflang_tags)} found
Links Found: {len(links)}
Images Found: {len(images)}

=== PAGE CONTENT ===
{text_content}

=== SAMPLE LINKS (first 20) ===
"""
        for i, link in enumerate(links[:20]):
            data_summary += f"{i+1}. {link['text']} -> {link['href']}\n"

        if hreflang_tags:
            data_summary += "\n=== HREFLANG TAGS ===\n"
            for tag in hreflang_tags[:10]:
                data_summary += f"- {tag['hreflang']}: {tag['href']}\n"

        # Build the final prompt
        prompt = f"""You are a website analysis assistant. Your task is to analyze website content and extract information based on the user's request.

USER REQUEST:
{user_prompt}

WEBSITE DATA:
{data_summary}

INSTRUCTIONS:
1. Carefully analyze the website content provided above
2. Extract or identify the information requested by the user
3. Provide a clear, structured response
4. If the requested information is not found, say so explicitly
5. Be concise but thorough
6. Use markdown formatting for better readability (bullet points, headers, etc.)
7. For internationalization/localization analysis, specifically look for:
   - Language tags and hreflang attributes
   - Language switchers or selectors
   - Multi-language content indicators
   - Translation-related features
   - Regional/country-specific content

Please provide your analysis now:
"""
        return prompt

    def analyze_url(self, url: str, prompt: str) -> Dict[str, Any]:
        """
        Main method to analyze a URL with a natural language prompt.

        Args:
            url: Website URL to analyze
            prompt: Natural language prompt describing what to analyze

        Returns:
            Dictionary containing analysis results and metadata
        """
        start_time = time.time()

        try:
            # Fetch website content
            website_data = self.fetch_website(url)

            # Analyze with AI
            analysis_result = self.analyze_with_ai(website_data, prompt)

            # Calculate analysis time
            analysis_time = round(time.time() - start_time, 2)
            analysis_result['metadata']['analysis_time'] = f"{analysis_time}s"

            return analysis_result

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'metadata': {
                    'url': url,
                    'analysis_time': f"{round(time.time() - start_time, 2)}s"
                }
            }


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
