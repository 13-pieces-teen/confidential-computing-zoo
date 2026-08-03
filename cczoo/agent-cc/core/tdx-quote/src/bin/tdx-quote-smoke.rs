use anyhow::{bail, Context, Result};
use base64::Engine as _;
use tdx_quote::tsm::TsmInstanceQuoteGenerator;
use tdx_quote::{QuoteGenerator, ReportData};

const SHA384_DIGEST_BYTES: usize = 48;

fn parse_digest(digest_hex: &str) -> Result<Vec<u8>> {
    let digest = hex::decode(digest_hex).context("digest must be hexadecimal")?;
    if digest.len() != SHA384_DIGEST_BYTES {
        bail!(
            "digest must be {SHA384_DIGEST_BYTES} bytes, got {}",
            digest.len()
        );
    }
    Ok(digest)
}

fn main() -> Result<()> {
    let digest_hex = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "00".repeat(SHA384_DIGEST_BYTES));
    let report_data = ReportData::from_digest(&parse_digest(&digest_hex)?)?;
    let quote = TsmInstanceQuoteGenerator::new().generate_quote(&report_data)?;
    let quote_bytes = base64::engine::general_purpose::STANDARD
        .decode(&quote.quote)
        .context("quote backend returned invalid base64")?;
    if quote_bytes.is_empty() {
        bail!("quote backend returned an empty quote");
    }

    println!("TDX Quote smoke test: PASS");
    println!("Quote format: {}", quote.quote_format);
    println!("Quote bytes: {}", quote_bytes.len());
    println!("REPORTDATA: {}", quote.report_data);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_sha384_digest() {
        assert_eq!(
            parse_digest(&"00".repeat(SHA384_DIGEST_BYTES))
                .unwrap()
                .len(),
            48
        );
    }

    #[test]
    fn rejects_wrong_length() {
        assert!(parse_digest("00")
            .unwrap_err()
            .to_string()
            .contains("48 bytes"));
    }

    #[test]
    fn rejects_non_hexadecimal_input() {
        assert!(parse_digest("zz")
            .unwrap_err()
            .to_string()
            .contains("hexadecimal"));
    }
}
