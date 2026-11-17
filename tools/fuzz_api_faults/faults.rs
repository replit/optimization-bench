//! Verifier-owned std::io schedules; usable by serde/Zrip typed drivers.
//! The library under test never parses the schedule or serializes observations.
use std::io::{self, Read, Write};

#[derive(Clone, Copy, Debug)]
pub struct FaultPlan {
    pub max_chunk: Option<usize>,
    pub fail_after: Option<usize>,
    pub zero_after: Option<usize>,
    pub eof_after: Option<usize>,
    pub interrupt_at: Option<usize>,
    pub error_kind: io::ErrorKind,
    pub flush_error: bool,
}

impl Default for FaultPlan {
    fn default() -> Self {
        Self { max_chunk: None, fail_after: None, zero_after: None,
            eof_after: None, interrupt_at: None, error_kind: io::ErrorKind::Other,
            flush_error: false }
    }
}

impl FaultPlan {
    pub fn validate(&self, writing: bool) -> io::Result<()> {
        if self.max_chunk == Some(0) || (writing && self.eof_after.is_some())
            || (!writing && self.flush_error)
            || !matches!(self.error_kind, io::ErrorKind::Other | io::ErrorKind::BrokenPipe
                | io::ErrorKind::PermissionDenied | io::ErrorKind::UnexpectedEof)
            || [self.fail_after, self.zero_after, self.eof_after].iter().filter(|x| x.is_some()).count() > 1 {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid verifier fault schedule"));
        }
        Ok(())
    }
}

#[derive(Debug)]
pub struct Schedule {
    pub plan: FaultPlan,
    pub position: usize,
    pub interrupted: bool,
    pub call_count: usize,
    pub error_count: usize,
    pub calls: Vec<(usize, usize)>,
}

impl Schedule {
    fn new(plan: FaultPlan, writing: bool) -> io::Result<Self> {
        plan.validate(writing)?;
        Ok(Self { plan, position: 0, interrupted: false, call_count: 0, error_count: 0, calls: Vec::new() })
    }
    fn length(&mut self, requested: usize) -> io::Result<usize> {
        self.call_count += 1;
        if requested == 0 { return Ok(0); }
        let p = self.plan;
        if p.interrupt_at.map_or(false, |at| self.position >= at) && !self.interrupted {
            self.interrupted = true;
            self.error_count += 1;
            return Err(io::Error::from(io::ErrorKind::Interrupted));
        }
        if p.fail_after.map_or(false, |at| self.position >= at) {
            self.error_count += 1;
            return Err(io::Error::from(p.error_kind));
        }
        if p.zero_after.map_or(false, |at| self.position >= at)
            || p.eof_after.map_or(false, |at| self.position >= at) { return Ok(0); }
        let mut n = requested.min(p.max_chunk.unwrap_or(usize::MAX));
        for boundary in [p.fail_after, p.zero_after, p.eof_after,
                if self.interrupted { None } else { p.interrupt_at }].iter().flatten() {
            if *boundary > self.position { n = n.min(*boundary - self.position); }
        }
        Ok(n)
    }
    fn accepted(&mut self, requested: usize, n: usize) {
        self.position += n;
        if self.calls.len() < 256 { self.calls.push((requested, n)); }
    }
}

pub struct FaultWriter {
    pub schedule: Schedule,
    pub bytes: Vec<u8>,
}
impl FaultWriter {
    pub fn new(plan: FaultPlan) -> io::Result<Self> {
        Ok(Self { schedule: Schedule::new(plan, true)?, bytes: Vec::new() })
    }
}
impl Write for FaultWriter {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        let n = self.schedule.length(data.len())?;
        self.bytes.extend_from_slice(&data[..n]);
        self.schedule.accepted(data.len(), n);
        Ok(n)
    }
    fn flush(&mut self) -> io::Result<()> {
        if self.schedule.plan.flush_error {
            self.schedule.error_count += 1;
            Err(io::Error::from(self.schedule.plan.error_kind))
        }
        else { Ok(()) }
    }
}

pub struct FaultReader {
    pub schedule: Schedule,
    pub data: Vec<u8>,
}
impl FaultReader {
    pub fn new(data: Vec<u8>, plan: FaultPlan) -> io::Result<Self> {
        Ok(Self { schedule: Schedule::new(plan, false)?, data })
    }
}
impl Read for FaultReader {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        let n = self.schedule.length(output.len())?.min(self.data.len() - self.schedule.position);
        output[..n].copy_from_slice(&self.data[self.schedule.position..self.schedule.position + n]);
        self.schedule.accepted(output.len(), n);
        Ok(n)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn short_and_interrupted_writes_preserve_all_bytes() {
        let mut w = FaultWriter::new(FaultPlan {
            max_chunk: Some(3), interrupt_at: Some(2), ..Default::default()
        }).unwrap();
        w.write_all(b"abcdef").unwrap();
        assert_eq!(w.bytes, b"abcdef");
        assert!(w.schedule.interrupted);
        assert_eq!(w.schedule.error_count, 1);
    }
    #[test]
    fn error_after_prefix_never_accepts_bytes_on_error() {
        let mut w = FaultWriter::new(FaultPlan { fail_after: Some(2), ..Default::default() }).unwrap();
        assert_eq!(w.write(b"abcdef").unwrap(), 2);
        assert_eq!(w.write(b"cdef").unwrap_err().kind(), io::ErrorKind::Other);
        assert_eq!(w.bytes, b"ab");
        assert_eq!(w.schedule.position, 2);
    }
    #[test]
    fn write_zero_is_not_successful_write_all() {
        let mut w = FaultWriter::new(FaultPlan { zero_after: Some(2), ..Default::default() }).unwrap();
        assert_eq!(w.write_all(b"abcdef").unwrap_err().kind(), io::ErrorKind::WriteZero);
        assert_eq!(w.bytes, b"ab");
    }
    #[test]
    fn flush_fault_is_not_implied_by_write() {
        let mut w = FaultWriter::new(FaultPlan { flush_error: true, ..Default::default() }).unwrap();
        w.write_all(b"data").unwrap();
        assert_eq!(w.schedule.error_count, 0);
        assert!(w.flush().is_err());
    }
    #[test]
    fn interrupted_reader_retries_once_without_losing_prefix() {
        let mut r = FaultReader::new(b"abcdef".to_vec(), FaultPlan {
            max_chunk: Some(3), interrupt_at: Some(2), ..Default::default()
        }).unwrap();
        let mut out = Vec::new();
        r.read_to_end(&mut out).unwrap();
        assert_eq!(out, b"abcdef");
        assert_eq!(r.schedule.error_count, 1);
    }
    #[test]
    fn reader_error_retains_partial_data() {
        let mut r = FaultReader::new(b"abcdef".to_vec(), FaultPlan {
            fail_after: Some(2), ..Default::default()
        }).unwrap();
        let mut out = Vec::new();
        assert!(r.read_to_end(&mut out).is_err());
        assert_eq!(out, b"ab");
    }
    #[test]
    fn reader_eof_is_not_error() {
        let mut r = FaultReader::new(b"abcdef".to_vec(), FaultPlan {
            eof_after: Some(2), ..Default::default()
        }).unwrap();
        let mut out = Vec::new();
        assert_eq!(r.read_to_end(&mut out).unwrap(), 2);
        assert_eq!(out, b"ab");
    }
    #[test]
    fn empty_io_does_not_consume_the_interruption() {
        let mut r = FaultReader::new(b"a".to_vec(), FaultPlan {
            interrupt_at: Some(0), ..Default::default()
        }).unwrap();
        assert_eq!(r.read(&mut []).unwrap(), 0);
        assert!(!r.schedule.interrupted);
        assert_eq!(r.read(&mut [0u8; 1]).unwrap_err().kind(), io::ErrorKind::Interrupted);
        assert_eq!(r.read(&mut [0u8; 1]).unwrap(), 1);
    }
    #[test]
    fn scheduled_read_error_can_fire_on_the_call_after_last_data() {
        let mut r = FaultReader::new(b"a".to_vec(), FaultPlan {
            fail_after: Some(1), ..Default::default()
        }).unwrap();
        assert_eq!(r.read(&mut [0u8; 1]).unwrap(), 1);
        assert!(r.read(&mut [0u8; 1]).is_err());
    }
    #[test]
    fn invalid_and_permanent_eintr_plans_are_rejected() {
        assert!(FaultWriter::new(FaultPlan { max_chunk: Some(0), ..Default::default() }).is_err());
        assert!(FaultReader::new(vec![], FaultPlan {
            error_kind: io::ErrorKind::Interrupted, fail_after: Some(0), ..Default::default()
        }).is_err());
        assert!(FaultReader::new(vec![], FaultPlan { flush_error: true, ..Default::default() }).is_err());
    }
}
